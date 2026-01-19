"""Qdrant server-based vector store backend.

This backend connects to a running Qdrant server instance.
It does not require external locking as the server handles concurrency.
Suitable for production multi-tenant deployments.
"""

from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue


class QdrantServerStore:
    """Qdrant server-based vector store backend.

    Connects to a Qdrant server for production deployments.
    The server handles concurrency internally, no external locking needed.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        api_key: str | None = None,
        https: bool = False,
        default_collection: str = "librarian_full",
    ):
        """Initialize Qdrant server connection.

        Args:
            host: Qdrant server hostname
            port: Qdrant server port
            api_key: Optional API key for authentication
            https: Whether to use HTTPS
            default_collection: Default collection name for llama_store property
        """
        self._host = host
        self._port = port
        self._api_key = api_key
        self._https = https
        self._default_collection = default_collection
        self._client: QdrantClient | None = None
        self._stores: dict[str, QdrantVectorStore] = {}

    @property
    def client(self) -> QdrantClient:
        """Lazy-initialize Qdrant client."""
        if self._client is None:
            self._client = QdrantClient(
                host=self._host,
                port=self._port,
                api_key=self._api_key,
                https=self._https,
            )
        return self._client

    @property
    def llama_store(self) -> QdrantVectorStore:
        """LlamaIndex VectorStore for the default collection."""
        return self.get_llama_store(self._default_collection)

    def get_llama_store(self, collection_name: str) -> QdrantVectorStore:
        """Get a LlamaIndex VectorStore for a specific collection."""
        if collection_name not in self._stores:
            self._stores[collection_name] = QdrantVectorStore(
                client=self.client,
                collection_name=collection_name,
            )
        return self._stores[collection_name]

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists on the server."""
        try:
            collections = self.client.get_collections().collections
            return any(c.name == collection_name for c in collections)
        except Exception:
            return False

    def get_indexed_ids(self, collection_name: str, id_field: str = "book_id") -> set[int]:
        """Get unique values of an ID field from a collection."""
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
            pass

    def requires_lock(self) -> bool:
        """Qdrant server handles concurrency, no external lock needed."""
        return False
