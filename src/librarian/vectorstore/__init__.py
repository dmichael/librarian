"""Vector store abstraction for Librarian.

Provides a unified interface for the supported backends:
- qdrant-file: Local Qdrant storage for development (requires locking)
- pgvector: PostgreSQL with pgvector extension (production, no locking needed)

Usage:
    from librarian.vectorstore import get_vector_store

    store = get_vector_store(config)
    indexed_books = store.get_indexed_ids("librarian_full")
    store.delete_by_filter("librarian_full", "book_id", 105)
"""

from pathlib import Path

from librarian.config import (
    DEFAULT_EMBED_DIM,
    DEFAULT_VECTOR_BACKEND,
    expand_path,
)
from librarian.vectorstore.protocol import LibrarianVectorStore

# Re-export protocol for type hints
__all__ = ["LibrarianVectorStore", "get_vector_store", "reset_vector_store"]


_vector_store_instance: LibrarianVectorStore | None = None


def reset_vector_store() -> None:
    """Clear the cached singleton. Intended for tests and reconfiguration."""
    global _vector_store_instance
    _vector_store_instance = None


def get_vector_store(
    config: dict,
    default_collection: str | None = None,
) -> LibrarianVectorStore:
    """Get the singleton vector store backend.

    Creates the store on first call; subsequent calls return the same instance.
    This prevents connection leaks when called from multiple threads.

    Args:
        config: Application configuration dict
        default_collection: Override default collection name (for llama_store property)

    Returns:
        A vector store backend implementing LibrarianVectorStore protocol

    Raises:
        ValueError: If backend type is unknown
        ImportError: If required dependencies are not installed (e.g., pgvector)
    """
    global _vector_store_instance
    if _vector_store_instance is not None:
        return _vector_store_instance

    vs_config = config.get("vector_store", {})
    backend = vs_config.get("backend", DEFAULT_VECTOR_BACKEND)

    # Get default collection from config if not overridden
    if default_collection is None:
        default_collection = vs_config.get("collection", "librarian_full")

    store: LibrarianVectorStore

    if backend == "qdrant-file":
        from librarian.vectorstore.qdrant_file import QdrantFileStore

        path = expand_path(vs_config.get("qdrant_path", "~/data/librarian/vectorstore/qdrant"))
        store = QdrantFileStore(path=path, default_collection=default_collection)

    elif backend == "pgvector":
        from librarian.vectorstore.pgvector_store import PgvectorStore

        store = PgvectorStore(
            connection_string=vs_config.get(
                "pgvector_url", "postgresql://localhost:5432/librarian"
            ),
            embed_dim=config.get("embedding", {}).get("dim", DEFAULT_EMBED_DIM),
            default_collection=default_collection,
        )

    else:
        raise ValueError(
            f"Unknown vector store backend: {backend}. "
            "Valid options: qdrant-file, pgvector"
        )

    _vector_store_instance = store
    return store


def get_collection_names(config: dict) -> dict[str, str]:
    """Get all collection names from config.

    Returns:
        Dict with keys: full, equations, chapters
    """
    vs_config = config.get("vector_store", {})
    return {
        "full": vs_config.get("collection", "librarian_full"),
        "equations": vs_config.get("equation_collection", "librarian_equations"),
        "chapters": vs_config.get("chapter_collection", "librarian_chapters"),
    }
