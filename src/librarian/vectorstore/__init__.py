"""Vector store abstraction for Librarian.

Provides a unified interface for different vector store backends:
- qdrant-file: Local Qdrant storage (default, requires locking)
- qdrant-server: Qdrant server for production (no locking needed)
- lancedb: Embedded LanceDB for development (no locking needed)
- chroma: Embedded Chroma for development (flexible metadata, no locking)
- pgvector: PostgreSQL with pgvector extension (production, no locking needed)

Usage:
    from librarian.vectorstore import get_vector_store

    store = get_vector_store(config)
    indexed_books = store.get_indexed_ids("librarian_full")
    store.delete_by_filter("librarian_full", "book_id", 105)
"""

from pathlib import Path

from librarian.config import expand_path
from librarian.vectorstore.protocol import LibrarianVectorStore

# Re-export protocol for type hints
__all__ = ["LibrarianVectorStore", "get_vector_store"]


def get_vector_store(
    config: dict,
    default_collection: str | None = None,
) -> LibrarianVectorStore:
    """Factory function to create the appropriate vector store backend.

    Args:
        config: Application configuration dict
        default_collection: Override default collection name (for llama_store property)

    Returns:
        A vector store backend implementing LibrarianVectorStore protocol

    Raises:
        ValueError: If backend type is unknown
        ImportError: If required dependencies are not installed (e.g., lancedb)
    """
    vs_config = config.get("vector_store", {})
    backend = vs_config.get("backend", "qdrant-file")

    # Get default collection from config if not overridden
    if default_collection is None:
        default_collection = vs_config.get("collection", "librarian_full")

    if backend == "qdrant-file":
        from librarian.vectorstore.qdrant_file import QdrantFileStore

        path = expand_path(vs_config.get("qdrant_path", "~/data/librarian/vectorstore/qdrant"))
        return QdrantFileStore(path=path, default_collection=default_collection)

    elif backend == "qdrant-server":
        from librarian.vectorstore.qdrant_server import QdrantServerStore

        return QdrantServerStore(
            host=vs_config.get("host", "localhost"),
            port=vs_config.get("port", 6333),
            api_key=vs_config.get("api_key"),
            https=vs_config.get("https", False),
            default_collection=default_collection,
        )

    elif backend == "lancedb":
        from librarian.vectorstore.lancedb_store import LanceDBStore

        uri = expand_path(vs_config.get("lancedb_path", "~/data/librarian/vectorstore/lancedb"))
        return LanceDBStore(uri=uri, default_collection=default_collection)

    elif backend == "chroma":
        from librarian.vectorstore.chroma_store import ChromaStore

        path = expand_path(vs_config.get("chroma_path", "~/data/librarian/vectorstore/chroma"))
        return ChromaStore(path=path, default_collection=default_collection)

    elif backend == "pgvector":
        from librarian.vectorstore.pgvector_store import PgvectorStore

        return PgvectorStore(
            connection_string=vs_config.get(
                "pgvector_url", "postgresql://localhost:5432/librarian"
            ),
            embed_dim=config.get("embedding", {}).get("dim", 768),
            default_collection=default_collection,
        )

    else:
        raise ValueError(
            f"Unknown vector store backend: {backend}. "
            "Valid options: qdrant-file, qdrant-server, lancedb, chroma, pgvector"
        )


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
