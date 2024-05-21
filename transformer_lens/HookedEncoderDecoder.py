"""Hooked EncoderDecoder

Contains a T5 style model. This is separate from :class:`transformer_lens.HookedTransformer`
because it has a significantly different architecture to e.g. GPT style transformers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, cast, overload

import torch
from einops import repeat
from jaxtyping import Float, Int
from torch import nn
from transformers import AutoTokenizer
from typing_extensions import Literal

import transformer_lens.loading_from_pretrained as loading
from transformer_lens.ActivationCache import ActivationCache
from transformer_lens.components import (
    Embed,
    Unembed,
    T5DecoderBlock,
    T5EncoderBlock,
    T5LayerNorm,
)
from transformer_lens.FactoredMatrix import FactoredMatrix
from transformer_lens.hook_points import HookedRootModule, HookPoint
from transformer_lens.HookedTransformerConfig import HookedTransformerConfig
from transformer_lens.utilities import devices


class HookedEncoderDecoder(HookedRootModule):
    """
    This class implements a T5 encoder-decoder using the components in ./components.py, with HookPoints on every interesting activation. It inherits from HookedRootModule.

    Limitations:
    - Also note that model does not include dropouts, which may lead to inconsistent results from training or fine-tuning.

    Like HookedTransformer, it can have a pretrained Transformer's weights loaded via `.from_pretrained`. There are a few features you might know from HookedTransformer which are not yet supported:
        - There is no preprocessing (e.g. LayerNorm folding) when loading a pretrained model
        - The model only accepts tokens as inputs, and not strings, or lists of strings
    """

    def __init__(self, cfg, tokenizer=None, move_to_device=True, **kwargs):
        super().__init__()
        if isinstance(cfg, Dict):
            cfg = HookedTransformerConfig(**cfg)
        elif isinstance(cfg, str):
            raise ValueError(
                "Please pass in a config dictionary or HookedTransformerConfig object. If you want to load a pretrained model, use HookedEncoderDecoder.from_pretrained() instead."
            )
        self.cfg = cfg

        assert (
            self.cfg.n_devices == 1
        ), "Multiple devices not supported for HookedEncoderDecoder"
        if tokenizer is not None:
            self.tokenizer = tokenizer
        elif self.cfg.tokenizer_name is not None:
            huggingface_token = os.environ.get("HF_TOKEN", None)
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.cfg.tokenizer_name,
                token=huggingface_token,
            )
        else:
            self.tokenizer = None

        if self.cfg.d_vocab == -1:
            # If we have a tokenizer, vocab size can be inferred from it.
            assert (
                self.tokenizer is not None
            ), "Must provide a tokenizer if d_vocab is not provided"
            self.cfg.d_vocab = max(self.tokenizer.vocab.values()) + 1
        if self.cfg.d_vocab_out == -1:
            self.cfg.d_vocab_out = self.cfg.d_vocab

        self.embed = Embed(self.cfg)
        self.encoder = nn.ModuleList(
            [
                T5EncoderBlock(self.cfg, num_layer)
                for num_layer in range(self.cfg.n_layers)
            ]
        )
        self.encoder_final_ln = T5LayerNorm(self.cfg)
        self.decoder = nn.ModuleList(
            [
                T5DecoderBlock(self.cfg, num_layer)
                for num_layer in range(self.cfg.n_layers)
            ]
        )
        self.decoder_final_ln = T5LayerNorm(self.cfg)
        # self.lm_head = nn.Linear(self.cfg.d_model, self.cfg.d_vocab_out)
        self.unembed = Unembed(self.cfg)

        self.hook_full_embed = HookPoint()

        if move_to_device:
            self.to(self.cfg.device)

        self.setup()

    @overload
    def forward(
        self,
        input: Int[torch.Tensor, "batch pos"],
        decoder_input: Int[torch.Tensor, "batch pos"],
        return_type: Literal["logits"],
        one_zero_attention_mask: Optional[Int[torch.Tensor, "batch pos"]] = None,
    ) -> Float[torch.Tensor, "batch pos d_vocab"]: ...

    @overload
    def forward(
        self,
        input: Int[torch.Tensor, "batch pos"],
        decoder_input: Int[torch.Tensor, "batch pos"],
        return_type: Literal[None],
        one_zero_attention_mask: Optional[Int[torch.Tensor, "batch pos"]] = None,
    ) -> Optional[Float[torch.Tensor, "batch pos d_vocab"]]: ...

    def forward(
        self,
        input: Int[torch.Tensor, "batch pos"],
        decoder_input: Int[torch.Tensor, "batch pos"],
        return_type: Optional[str] = "logits",
        one_zero_attention_mask: Optional[Int[torch.Tensor, "batch pos"]] = None,
    ) -> Optional[Float[torch.Tensor, "batch pos d_vocab"]]:
        """Input must be a batch of tokens. Strings and lists of strings are not yet supported.

        return_type Optional[str]: The type of output to return. Can be one of: None (return nothing, don't calculate logits), or 'logits' (return logits).

        token_type_ids Optional[torch.Tensor]: Binary ids indicating whether a token belongs to sequence A or B. For example, for two sentences: "[CLS] Sentence A [SEP] Sentence B [SEP]", token_type_ids would be [0, 0, ..., 0, 1, ..., 1, 1]. `0` represents tokens from Sentence A, `1` from Sentence B. If not provided, BERT assumes a single sequence input. Typically, shape is (batch_size, sequence_length).

        one_zero_attention_mask: Optional[torch.Tensor]: A binary mask which indicates which tokens should be attended to (1) and which should be ignored (0). Primarily used for padding variable-length sentences in a batch. For instance, in a batch with sentences of differing lengths, shorter sentences are padded with 0s on the right. If not provided, the model assumes all tokens should be attended to.
        """

        tokens = input

        if tokens.device.type != self.cfg.device:
            tokens = tokens.to(self.cfg.device)
            if one_zero_attention_mask is not None:
                one_zero_attention_mask = one_zero_attention_mask.to(self.cfg.device)

        resid = self.hook_full_embed(self.embed(tokens))

        large_negative_number = -torch.inf
        mask = (
            repeat(1 - one_zero_attention_mask, "batch pos -> batch 1 1 pos")
            if one_zero_attention_mask is not None
            else None
        )

        for encoder_block in self.encoder:
            resid = encoder_block(resid)

        resid = self.encoder_final_ln(resid)

        decoder_resid = self.embed(decoder_input)

        for decoder_block in self.decoder:
            decoder_resid = decoder_block(decoder_resid, resid)

        decoder_resid = self.decoder_final_ln(decoder_resid)
        if return_type is None:
            return None

        logits = self.unembed(decoder_resid)
        return logits

    @overload
    def run_with_cache(
        self, *model_args, return_cache_object: Literal[True] = True, **kwargs
    ) -> Tuple[Float[torch.Tensor, "batch pos d_vocab"], ActivationCache]: ...

    @overload
    def run_with_cache(
        self, *model_args, return_cache_object: Literal[False], **kwargs
    ) -> Tuple[Float[torch.Tensor, "batch pos d_vocab"], Dict[str, torch.Tensor]]: ...

    def run_with_cache(
        self,
        *model_args,
        return_cache_object: bool = True,
        remove_batch_dim: bool = False,
        **kwargs,
    ) -> Tuple[
        Float[torch.Tensor, "batch pos d_vocab"],
        Union[ActivationCache, Dict[str, torch.Tensor]],
    ]:
        """
        Wrapper around run_with_cache in HookedRootModule. If return_cache_object is True, this will return an ActivationCache object, with a bunch of useful HookedTransformer specific methods, otherwise it will return a dictionary of activations as in HookedRootModule. This function was copied directly from HookedTransformer.
        """
        out, cache_dict = super().run_with_cache(
            *model_args, remove_batch_dim=remove_batch_dim, **kwargs
        )
        if return_cache_object:
            cache = ActivationCache(
                cache_dict, self, has_batch_dim=not remove_batch_dim
            )
            return out, cache
        else:
            return out, cache_dict

    def to(  # type: ignore
        self,
        device_or_dtype: Union[torch.device, str, torch.dtype],
        print_details: bool = True,
    ):
        return devices.move_to_and_update_config(self, device_or_dtype, print_details)

    def cuda(self):
        # Wrapper around cuda that also changes self.cfg.device
        return self.to("cuda")

    def cpu(self):
        # Wrapper around cuda that also changes self.cfg.device
        return self.to("cpu")

    def mps(self):
        # Wrapper around cuda that also changes self.cfg.device
        return self.to("mps")

    @classmethod
    def from_pretrained(
        cls,
        model_name: str,
        checkpoint_index: Optional[int] = None,
        checkpoint_value: Optional[int] = None,
        hf_model=None,
        device: Optional[str] = None,
        tokenizer=None,
        move_to_device=True,
        dtype=torch.float32,
        **from_pretrained_kwargs,
    ) -> HookedEncoderDecoder:
        """Loads in the pretrained weights from huggingface. Currently supports loading weight from HuggingFace BertForMaskedLM. Unlike HookedTransformer, this does not yet do any preprocessing on the model."""
        logging.warning(
            "Support for T5 in TransformerLens is currently experimental, until such a time when it has feature "
            "parity with HookedTransformer and has been tested on real research tasks. Until then, backward "
            "compatibility is not guaranteed. Please see the docs for information on the limitations of the current "
            "implementation."
            "\n"
            "If using BERT for interpretability research, keep in mind that BERT has some significant architectural "
            "differences to GPT. For example, LayerNorms are applied *after* the attention and MLP components, meaning "
            "that the last LayerNorm in a block cannot be folded."
        )

        assert not (
            from_pretrained_kwargs.get("load_in_8bit", False)
            or from_pretrained_kwargs.get("load_in_4bit", False)
        ), "Quantization not supported"

        if "torch_dtype" in from_pretrained_kwargs:
            dtype = from_pretrained_kwargs["torch_dtype"]

        name_or_path = (
            model_name
            if Path(model_name).exists
            else loading.get_official_model_name(model_name)
        )

        cfg = loading.get_pretrained_model_config(
            name_or_path,
            checkpoint_index=checkpoint_index,
            checkpoint_value=checkpoint_value,
            fold_ln=False,
            device=device,
            n_devices=1,
            dtype=dtype,
            **from_pretrained_kwargs,
        )

        state_dict = loading.get_pretrained_state_dict(
            name_or_path, cfg, hf_model, dtype=dtype, **from_pretrained_kwargs
        )

        model = cls(cfg, tokenizer, move_to_device=False)

        model.load_state_dict(state_dict, strict=False)

        if move_to_device:
            model.to(cfg.device)

        print(f"Loaded pretrained model {model_name} into HookedTransformer")

        return model

    @property
    def W_U(self) -> Float[torch.Tensor, "d_model d_vocab"]:
        """
        Convenience to get the unembedding matrix (ie the linear map from the final residual stream to the output logits)
        """
        return self.unembed.W_U

    @property
    def b_U(self) -> Float[torch.Tensor, "d_vocab"]:
        """
        Convenience to get the unembedding bias
        """
        return self.unembed.b_U

    @property
    def W_E(self) -> Float[torch.Tensor, "d_vocab d_model"]:
        """
        Convenience to get the embedding matrix
        """
        return self.embed.embed.W_E

    @property
    def W_pos(self) -> Float[torch.Tensor, "n_ctx d_model"]:
        """
        Convenience function to get the positional embedding. Only works on models with absolute positional embeddings!
        """
        return self.embed.pos_embed.W_pos

    @property
    def W_E_pos(self) -> Float[torch.Tensor, "d_vocab+n_ctx d_model"]:
        """
        Concatenated W_E and W_pos. Used as a full (overcomplete) basis of the input space, useful for full QK and full OV circuits.
        """
        return torch.cat([self.W_E, self.W_pos], dim=0)

    @property
    def W_K(self) -> Float[torch.Tensor, "n_layers n_heads d_model d_head"]:
        """Stacks the key weights across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).attn.W_K for block in self.encoder]
            + [cast(T5DecoderBlock, block).attn.W_K for block in self.decoder],
            dim=0,
        )

    @property
    def W_Q(self) -> Float[torch.Tensor, "n_layers n_heads d_model d_head"]:
        """Stacks the query weights across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).attn.W_Q for block in self.encoder]
            + [cast(T5DecoderBlock, block).attn.W_Q for block in self.decoder],
            dim=0,
        )

    @property
    def W_V(self) -> Float[torch.Tensor, "n_layers n_heads d_model d_head"]:
        """Stacks the value weights across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).attn.W_V for block in self.encoder]
            + [cast(T5DecoderBlock, block).attn.W_V for block in self.decoder],
            dim=0,
        )

    @property
    def W_O(self) -> Float[torch.Tensor, "n_layers n_heads d_head d_model"]:
        """Stacks the attn output weights across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).attn.W_O for block in self.encoder]
            + [cast(T5DecoderBlock, block).attn.W_O for block in self.decoder],
            dim=0,
        )

    @property
    def W_in(self) -> Float[torch.Tensor, "n_layers d_model d_mlp"]:
        """Stacks the MLP input weights across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).mlp.W_in for block in self.encoder]
            + [cast(T5DecoderBlock, block).mlp.W_in for block in self.decoder],
            dim=0,
        )

    @property
    def W_out(self) -> Float[torch.Tensor, "n_layers d_mlp d_model"]:
        """Stacks the MLP output weights across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).mlp.W_out for block in self.encoder]
            + [cast(T5DecoderBlock, block).mlp.W_out for block in self.decoder],
            dim=0,
        )

    @property
    def b_K(self) -> Float[torch.Tensor, "n_layers n_heads d_head"]:
        """Stacks the key biases across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).attn.b_K for block in self.encoder]
            + [cast(T5DecoderBlock, block).attn.b_K for block in self.decoder],
            dim=0,
        )

    @property
    def b_Q(self) -> Float[torch.Tensor, "n_layers n_heads d_head"]:
        """Stacks the query biases across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).attn.b_Q for block in self.encoder]
            + [cast(T5DecoderBlock, block).attn.b_Q for block in self.decoder],
            dim=0,
        )

    @property
    def b_V(self) -> Float[torch.Tensor, "n_layers n_heads d_head"]:
        """Stacks the value biases across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).attn.b_V for block in self.encoder]
            + [cast(T5DecoderBlock, block).attn.b_V for block in self.decoder],
            dim=0,
        )

    @property
    def b_O(self) -> Float[torch.Tensor, "n_layers d_model"]:
        """Stacks the attn output biases across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).attn.b_O for block in self.encoder]
            + [cast(T5DecoderBlock, block).attn.b_O for block in self.decoder],
            dim=0,
        )

    @property
    def b_in(self) -> Float[torch.Tensor, "n_layers d_mlp"]:
        """Stacks the MLP input biases across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).mlp.b_in for block in self.encoder]
            + [cast(T5DecoderBlock, block).mlp.b_in for block in self.decoder],
            dim=0,
        )

    @property
    def b_out(self) -> Float[torch.Tensor, "n_layers d_model"]:
        """Stacks the MLP output biases across all layers"""
        return torch.stack(
            [cast(T5EncoderBlock, block).mlp.b_out for block in self.encoder]
            + [cast(T5DecoderBlock, block).mlp.b_out for block in self.decoder],
            dim=0,
        )

    @property
    def QK(self) -> FactoredMatrix:  # [n_layers, n_heads, d_model, d_model]
        """Returns a FactoredMatrix object with the product of the Q and K matrices for each layer and head.
        Useful for visualizing attention patterns."""
        return FactoredMatrix(self.W_Q, self.W_K.transpose(-2, -1))

    @property
    def OV(self) -> FactoredMatrix:  # [n_layers, n_heads, d_model, d_model]
        """Returns a FactoredMatrix object with the product of the O and V matrices for each layer and head."""
        return FactoredMatrix(self.W_V, self.W_O)

    def all_head_labels(self) -> List[str]:
        """Returns a list of strings with the format "L{l}H{h}", where l is the layer index and h is the head index."""
        return [
            f"EL{l}H{h}"
            for l in range(self.cfg.n_layers)
            for h in range(self.cfg.n_heads)
        ] + [
            f"DL{l}H{h}"
            for l in range(self.cfg.n_layers)
            for h in range(self.cfg.n_heads)
        ]