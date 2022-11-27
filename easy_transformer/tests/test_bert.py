# %%

import pytest
import torch

# trying to import [AutoModelForMaskedLM] from the non-private location fucks up, not sure why; it makes
# [from_pretrained == None]
from transformers import AutoModelForMaskedLM
from transformers.modeling_outputs import MaskedLMOutput
from transformers.models.auto.tokenization_auto import AutoTokenizer

from easy_transformer import EasyBERT


def test_bert():
    model_name = "bert-base-uncased"
    text = "Hello world!"
    model = EasyBERT.EasyBERT.from_pretrained(model_name)  # TODO why two?
    output: MaskedLMOutput = model(text)  # TODO need to change the type

    assert output.logits.shape == (1, 5, model.config.d_vocab)  # TODO why 5?

    # now let's compare it to the HuggingFace version
    assert (
        AutoModelForMaskedLM.from_pretrained is not None
    )  # recommended by https://github.com/microsoft/pylance-release/issues/333#issuecomment-688522371
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForMaskedLM.from_pretrained(model_name)
    hf_output = model(**tokenizer(text, return_tensors="pt"))
    assert torch.allclose(output.logits, hf_output.logits, atol=1e-4)


def test_embeddings():
    hf = AutoModelForMaskedLM.from_pretrained("bert-base-uncased")
    model = EasyBERT.EasyBERT.from_pretrained("bert-base-uncased")
    assert torch.allclose(
        hf.bert.embeddings.word_embeddings.weight,
        model.embeddings.word_embeddings.weight,
        atol=1e-4,
    )


# %%


def make_this_a_test():
    # TODO make an anki about this workflow- including function scope for name conflicts

    import torch
    from transformers import AutoModelForMaskedLM
    from transformers.modeling_outputs import MaskedLMOutput
    from transformers.models.auto.tokenization_auto import AutoTokenizer

    from easy_transformer import EasyBERT

    hf = AutoModelForMaskedLM.from_pretrained("bert-base-uncased")
    model = EasyBERT.EasyBERT.from_pretrained("bert-base-uncased")

    assert torch.allclose(
        hf.bert.embeddings.word_embeddings.weight,
        model.embeddings.word_embeddings.weight,
        atol=1e-4,
    )


# %%

# now we test output
# TODO ensure that our model matches the architecture diagram of BERT

# autoreload 2


def make_this_a_test():
    model_name = "bert-base-uncased"
    text = "Hello world!"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    our_model = EasyBERT.EasyBERT.from_pretrained(model_name)  # TODO why two?
    output: MaskedLMOutput = our_model(text)  # TODO need to change the type

    n_tokens_in_input = tokenizer(text, return_tensors="pt")
    assert n_tokens_in_input == 5
    assert output.logits.shape == (1, n_tokens_in_input, our_model.config.d_vocab)

    assert (
        AutoModelForMaskedLM.from_pretrained is not None
    )  # recommended by https://github.com/microsoft/pylance-release/issues/333#issuecomment-688522371
    hugging_face_model = AutoModelForMaskedLM.from_pretrained(model_name)
    hf_output = hugging_face_model(**tokenizer(text, return_tensors="pt"))
    assert torch.allclose(output.logits, hf_output.logits, atol=1e-4)


# %%


def test_that_im_awesome():
    model_name = "bert-base-uncased"
    text = "Hello world!"
    from transformers.models.auto.tokenization_auto import AutoTokenizer

    from easy_transformer import EasyBERT

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    our_model = EasyBERT.EasyBERT.from_pretrained(model_name)  # TODO why two?
    # TODO add various [return_type] options
    # TODO figure out what's up with [using eos_token] and [using bos_token]
    output = our_model(text)  # TODO set the type of the variable on the LHS

    # trying to import [AutoModelForMaskedLM] from the non-private location fucks up, not sure why; it makes
    # [from_pretrained == None]
    from transformers import AutoModelForMaskedLM

    n_tokens_in_input = tokenizer(text, return_tensors="pt")["input_ids"].shape[1]
    assert n_tokens_in_input == 5
    # TODO make this output.logits to match the other test
    # TODO need to add an unembed
    assert output.shape == (1, n_tokens_in_input, our_model.config.d_vocab)

    assert (
        AutoModelForMaskedLM.from_pretrained is not None
    )  # recommended by https://github.com/microsoft/pylance-release/issues/333#issuecomment-688522371
    hugging_face_model = AutoModelForMaskedLM.from_pretrained(model_name)
    hf_output = hugging_face_model(**tokenizer(text, return_tensors="pt"))
    import torch

    assert torch.allclose(output.logits, hf_output.logits, atol=1e-4)
