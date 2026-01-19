"""Qdrant file-based vector store backend.

This backend stores vectors in local Qdrant storage (SQLite-based).
It requires external file locking for concurrent access as the local
storage does not support multiple concurrent clients.
"""

from pathlib import Path

from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue


class QdrantFileStore:
    """Qdrant file-based vector store backend.

    This wraps the existing Qdrant local storage implementation.
    It requires external locking (fcntl) for concurrent access.
    """

    def __init__(self, path: Path, default_collection: str = "librarian_full"):
        """Initialize Qdrant file-based store.

        Args:
            path: Path to Qdrant storage directory
            default_collection: Default collection name for llama_store property
        """
        self._path = path
        self._default_collection = default_collection
        self._client: QdrantClient | None = None
        self._stores: dict[str, QdrantVectorStore] = {}

        # Ensure directory exists
        path.mkdir(parents=True, exist_ok=True)

    @property
    def client(self) -> QdrantClient:
        """Lazy-initialize Qdrant client."""
        if self._client is None:
            self._client = QdrantClient(path=str(self._path))
        return self._client

    @property
    def llama_store(self) -> QdrantVectorStore:
        """LlamaIndex VectorStore for the default collection."""
        return self.get_llama_store(self._default_collection)

    def get_llama_store(self, collection_name: str) -> QdrantVectorStore:
        """Get a LlamaIndex VectorStore for a specific collection.

        Caches stores to avoid creating multiple instances for the same collection.
        """
        if collection_name not in self._stores:
            self._stores[collection_name] = QdrantVectorStore(
                client=self.client,
                collection_name=collection_name,
            )
        return self._stores[collection_name]

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists."""
        try:
            collections = self.client.get_collections().collections
            return any(c.name == collection_name for c in collections)
        except Exception:
            return False

    def get_indexed_ids(self, collection_name: str, id_field: str = "book_id") -> set[int]:
        """Get unique values of an ID field from a collection.

        Scrolls through all documents to extract unique IDs.
        """
        try:
            if not self.collection_exists(collection_name):
                return set()

            indexed = set()
            offset = None

            while True:
                results, offset = self.client.scroll(
                    collection_name=collection_name,
                    limit=1000,
                    offset=offset,
                    with_payload=[id_field],
                )

                if not results:
                    break

                for point in results:
                    if point.payload and id_field in point.payload:
                        indexed.add(point.payload[id_field])

                if offset is None:
                    break

            return indexed
        except Exception:
            return set()

    def delete_by_filter(self, collection_name: str, field: str, value: int) -> None:
        """Delete documents where field equals value."""
        try:
            if not self.collection_exists(collection_name):
                return

            self.client.delete(
                collection_name=collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[FieldCondition(key=field, match=MatchValue(value=value))]
                    )
                ),
            )
        except Exception:
            pass  # Collection might not exist

    def requires_lock(self) -> bool:
        """Qdrant file-based storage requires external locking."""
        return True
