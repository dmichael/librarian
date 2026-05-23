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

    def get_collection_count(self, collection_name: str) -> int:
        """Get number of documents in a collection."""
        try:
            if not self.collection_exists(collection_name):
                return 0
            return self.client.count(collection_name).count
        except Exception:
            return 0

    def get_metadata_counts(self, collection_name: str, field: str) -> dict[str, int]:
        """Count occurrences of each distinct value for a metadata field."""
        try:
            if not self.collection_exists(collection_name):
                return {}

            counts: dict[str, int] = {}
            offset = None

            while True:
                results, offset = self.client.scroll(
                    collection_name=collection_name,
                    limit=1000,
                    offset=offset,
                    with_payload=[field],
                )
                if not results:
                    break
                for point in results:
                    val = (point.payload or {}).get(field, "Unknown")
                    counts[str(val)] = counts.get(str(val), 0) + 1
                if offset is None:
                    break

            return counts
        except Exception:
            return {}

    def get_documents_by_filter(
        self, collection_name: str, filters: dict[str, any]
    ) -> list[tuple[str, dict]]:
        """Get documents matching metadata filters."""
        try:
            if not self.collection_exists(collection_name):
                return []

            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
            ]
            results_list = []
            offset = None

            while True:
                results, offset = self.client.scroll(
                    collection_name=collection_name,
                    limit=1000,
                    offset=offset,
                    scroll_filter=Filter(must=conditions),
                    with_payload=True,
                )
                if not results:
                    break
                for point in results:
                    text = (point.payload or {}).get("text", "")
                    results_list.append((text, dict(point.payload or {})))
                if offset is None:
                    break

            return results_list
        except Exception:
            return []

    def update_metadata_by_book_id(
        self, collection_name: str, book_id: int, updates: dict[str, str]
    ) -> int:
        """Update payload fields on all points belonging to a book."""
        if not updates or not self.collection_exists(collection_name):
            return 0
        try:
            flt = Filter(
                must=[FieldCondition(key="book_id", match=MatchValue(value=book_id))]
            )
            matched = self.client.count(
                collection_name=collection_name, count_filter=flt
            ).count
            if matched == 0:
                return 0
            self.client.set_payload(
                collection_name=collection_name,
                payload=dict(updates),
                points=FilterSelector(filter=flt),
            )
            return matched
        except Exception:
            return 0

    def requires_lock(self) -> bool:
        """Qdrant server handles concurrency, no external lock needed."""
        return False
