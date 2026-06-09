"""Single owner of embedding-model construction.

Every code path that embeds text (indexing, querying, the MCP server) must
get its model from here so index-time and query-time embeddings can never
drift, and so the model is constructed once per process instead of per call.
"""

import threading

from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from librarian.config import DEFAULT_EMBED_MODEL

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_model: HuggingFaceEmbedding | None = None
_model_key: tuple | None = None
_lock = threading.Lock()


def get_embed_model(config: dict) -> HuggingFaceEmbedding:
    """Get the process-wide embedding model, constructing it on first use.

    Thread-safe; concurrent callers block while the model loads (a few
    seconds). If the embedding config changes between calls the model is
    rebuilt, otherwise the cached instance is returned.
    """
    global _model, _model_key

    emb = config.get("embedding", {})
    model_name = emb.get("model", DEFAULT_EMBED_MODEL)
    device = emb.get("device", "cpu")
    batch_size = emb.get("batch_size", 48)
    key = (model_name, device, batch_size)

    if _model is not None and _model_key == key:
        return _model

    with _lock:
        if _model is not None and _model_key == key:
            return _model

        kwargs: dict = {
            "model_name": model_name,
            "device": device,
            "embed_batch_size": batch_size,
        }
        # BGE models need the query instruction prefix for retrieval queries
        if "bge" in model_name.lower():
            kwargs["query_instruction"] = BGE_QUERY_INSTRUCTION

        _model = HuggingFaceEmbedding(**kwargs)
        _model_key = key

    return _model
