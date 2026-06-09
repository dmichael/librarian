"""Qdrant file-based vector store backend.

This backend stores vectors in local Qdrant storage (SQLite-based).
It requires external file locking for concurrent access as the local
storage does not support multiple concurrent clients.
"""

import json
from pathlib import Path

from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue


def _payload_text(payload: dict) -> str:
    """Extract document text from a qdrant payload.

    LlamaIndex stores text either as a top-level 'text' key or inside the
    serialized '_node_content' JSON depending on version.
    """
    text = payload.get("text")
    if text:
        return text
    raw = payload.get("_node_content")
    if isinstance(raw, str):
        try:
            return json.loads(raw).get("text", "") or ""
        except json.JSONDecodeError:
            pass
    return ""


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
        collections = self.client.get_collections().collections
        return any(c.name == collection_name for c in collections)

    def get_indexed_ids(self, collection_name: str, id_field: str = "book_id") -> set[int]:
        """Get unique values of an ID field from a collection.

        Scrolls through all documents to extract unique IDs.
        """
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

    def delete_by_filter(self, collection_name: str, field: str, value: int) -> None:
        """Delete documents where field equals value."""
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

    def get_collection_count(self, collection_name: str) -> int:
        """Get number of documents in a collection."""
        if not self.collection_exists(collection_name):
            return 0
        return self.client.count(collection_name).count

    def get_metadata_counts(self, collection_name: str, field: str) -> dict[str, int]:
        """Count occurrences of each distinct value for a metadata field."""
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

    def get_documents_by_filter(
        self, collection_name: str, filters: dict[str, any]
    ) -> list[tuple[str, dict]]:
        """Get documents matching metadata filters."""
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
                payload = dict(point.payload or {})
                results_list.append((_payload_text(payload), payload))
            if offset is None:
                break

        return results_list

    def text_search(
        self,
        collection_name: str,
        query: str,
        book_id: int | None = None,
        library: str | None = None,
        limit: int = 10,
    ) -> list[tuple[str, dict]]:
        """Literal substring search (case-insensitive) via full scroll.

        Local qdrant has no server-side substring matching, so this scans
        point payloads and matches in Python.
        """
        if not self.collection_exists(collection_name):
            return []

        must = []
        if book_id is not None:
            must.append(FieldCondition(key="book_id", match=MatchValue(value=book_id)))
        if library:
            must.append(FieldCondition(key="library", match=MatchValue(value=library)))

        needle = query.lower()
        matches: list[tuple[str, dict]] = []
        offset = None

        while True:
            results, offset = self.client.scroll(
                collection_name=collection_name,
                limit=1000,
                offset=offset,
                scroll_filter=Filter(must=must) if must else None,
                with_payload=True,
            )
            if not results:
                break
            for point in results:
                payload = dict(point.payload or {})
                text = _payload_text(payload)
                if needle in text.lower():
                    matches.append((text, payload))
                    if len(matches) >= limit:
                        return matches
            if offset is None:
                break

        return matches

    def update_metadata_by_book_id(
        self, collection_name: str, book_id: int, updates: dict[str, str]
    ) -> int:
        """Update payload fields on all points belonging to a book."""
        if not updates or not self.collection_exists(collection_name):
            return 0

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

    def requires_lock(self) -> bool:
        """Qdrant file-based storage requires external locking."""
        return True
