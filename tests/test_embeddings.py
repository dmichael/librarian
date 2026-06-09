"""Tests for the shared embedding-model factory."""

import pytest

pytest.importorskip("llama_index")

import librarian.embeddings as embeddings


class _StubModel:
    """Stands in for HuggingFaceEmbedding so tests don't load a real model."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def stub_model(monkeypatch):
    monkeypatch.setattr(embeddings, "HuggingFaceEmbedding", _StubModel)
    monkeypatch.setattr(embeddings, "_model", None)
    monkeypatch.setattr(embeddings, "_model_key", None)


def test_same_config_returns_cached_instance():
    config = {"embedding": {"model": "BAAI/bge-base-en-v1.5", "device": "cpu"}}

    first = embeddings.get_embed_model(config)
    second = embeddings.get_embed_model(config)

    assert first is second


def test_bge_models_get_query_instruction():
    config = {"embedding": {"model": "BAAI/bge-base-en-v1.5"}}

    model = embeddings.get_embed_model(config)

    assert model.kwargs["query_instruction"] == embeddings.BGE_QUERY_INSTRUCTION


def test_non_bge_models_get_no_query_instruction():
    config = {"embedding": {"model": "sentence-transformers/all-MiniLM-L6-v2"}}

    model = embeddings.get_embed_model(config)

    assert "query_instruction" not in model.kwargs


def test_config_change_rebuilds_model():
    first = embeddings.get_embed_model({"embedding": {"model": "BAAI/bge-base-en-v1.5"}})
    second = embeddings.get_embed_model({"embedding": {"device": "mps"}})

    assert first is not second
    assert second.kwargs["device"] == "mps"
