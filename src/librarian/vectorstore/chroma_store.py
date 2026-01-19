"""Chroma vector store backend.

Chroma is an embedded vector database with:
- Flexible metadata handling (handles lists, nulls)
- Built-in persistence
- Good performance for development workloads

Note: Chroma is an optional dependency. Install with:
    pip install -e ".[chroma]"
"""

from pathlib import Path

try:
    import chromadb
    from llama_index.vector_stores.chroma import ChromaVectorStore

    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    chromadb = None
    ChromaVectorStore = None


class ChromaStore:
    """Chroma vector store backend.

    Embedded database suitable for development. Supports flexible metadata
    without the schema constraints of LanceDB.
    """

    def __init__(self, path: Path | str, default_collection: str = "librarian_full"):
        """Initialize Chroma store.

        Args:
            path: Path to Chroma storage directory
            default_collection: Default collection name for llama_store property

        Raises:
            ImportError: If chromadb is not installed
        """
        if not CHROMA_AVAILABLE:
            raise ImportError(
                "Chroma is not installed. Install with: pip install -e '.[chroma]'"
            )

        self._path = Path(path) if isinstance(path, str) else path
        self._default_collection = default_collection
        self._client: "chromadb.PersistentClient" | None = None
        self._stores: dict[str, "ChromaVectorStore"] = {}

        # Ensure directory exists
        self._path.mkdir(parents=True, exist_ok=True)

    @property
    def client(self) -> "chromadb.PersistentClient":
        """Lazy-initialize Chroma client."""
        if self._client is None:
            self._client = chromadb.PersistentClient(path=str(self._path))
        return self._client

    @property
    def llama_store(self) -> "ChromaVectorStore":
        """LlamaIndex VectorStore for the default collection."""
        return self.get_llama_store(self._default_collection)

    def get_llama_store(self, collection_name: str) -> "ChromaVectorStore":
        """Get a LlamaIndex VectorStore for a specific collection.

        Creates the collection if it doesn't exist.
        """
        if collection_name not in self._stores:
            collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._stores[collection_name] = ChromaVectorStore(chroma_collection=collection)
        return self._stores[collection_name]

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists in Chroma."""
        try:
            self.client.get_collection(collection_name)
            return True
        except Exception:
            return False

    def get_indexed_ids(self, collection_name: str, id_field: str = "book_id") -> set[int]:
        """Get unique values of an ID field from a collection.

        Uses Chroma's native get() with pagination for efficient ID extraction.
        """
        try:
            if not self.collection_exists(collection_name):
                return set()

            collection = self.client.get_collection(collection_name)
            indexed = set()
            offset = 0
            limit = 1000

            while True:
                result = collection.get(offset=offset, limit=limit, include=["metadatas"])

                if not result["ids"]:
                    break

                for metadata in result["metadatas"]:
                    if metadata and id_field in metadata:
                        indexed.add(metadata[id_field])

                if len(result["ids"]) < limit:
                    break
                offset += limit

            return indexed
        except Exception:
            return set()

    def delete_by_filter(self, collection_name: str, field: str, value: int) -> None:
        """Delete documents where field equals value.

        Chroma uses JSON-style where clauses for deletion.
        """
        try:
            if not self.collection_exists(collection_name):
                return

            collection = self.client.get_collection(collection_name)
            collection.delete(where={field: {"$eq": value}})
        except Exception:
            pass

    def requires_lock(self) -> bool:
        """Chroma handles concurrency internally."""
        return False
