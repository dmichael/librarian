"""pgvector (PostgreSQL) vector store backend.

Uses llama-index-vector-stores-postgres for LlamaIndex integration and
raw psycopg connections for direct SQL operations (collection checks,
ID retrieval, filtered deletes).

Note: pgvector is an optional dependency. Install with:
    pip install -e ".[pgvector]"
"""

import json
import logging

try:
    from llama_index.vector_stores.postgres import PGVectorStore

    PGVECTOR_AVAILABLE = True
except ImportError:
    PGVECTOR_AVAILABLE = False
    PGVectorStore = None

try:
    import psycopg
    from psycopg.types.json import Jsonb

    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False
    Jsonb = None
    psycopg = None

logger = logging.getLogger(__name__)


def _metadata_with_node_content_updates(metadata: dict, updates: dict[str, str]) -> dict:
    """Apply metadata updates to both pgvector metadata and LlamaIndex node JSON."""
    updated = dict(metadata or {})
    updated.update(updates)

    raw_node_content = updated.get("_node_content")
    if not isinstance(raw_node_content, str):
        return updated

    try:
        node_content = json.loads(raw_node_content)
    except json.JSONDecodeError:
        return updated

    node_metadata = node_content.get("metadata")
    if not isinstance(node_metadata, dict):
        return updated

    node_metadata.update(updates)
    updated["_node_content"] = json.dumps(node_content)
    return updated


class PgvectorStore:
    """pgvector (PostgreSQL) vector store backend.

    Production backend using PostgreSQL with the pgvector extension.
    The server handles concurrency internally, no external locking needed.

    LlamaIndex's PGVectorStore manages table creation and vector operations.
    Raw psycopg is used for metadata queries and bulk deletes to keep those
    operations simple and avoid SQLAlchemy overhead.
    """

    def __init__(
        self,
        connection_string: str = "postgresql://localhost:5432/librarian",
        embed_dim: int = 768,
        default_collection: str = "librarian_full",
    ):
        if not PGVECTOR_AVAILABLE:
            raise ImportError(
                "llama-index-vector-stores-postgres is not installed. "
                "Install with: pip install -e '.[pgvector]'"
            )
        if not PSYCOPG_AVAILABLE:
            raise ImportError(
                "psycopg is not installed. Install with: pip install -e '.[pgvector]'"
            )

        self._connection_string = connection_string
        self._embed_dim = embed_dim
        self._default_collection = default_collection
        self._stores: dict[str, "PGVectorStore"] = {}
        self._psycopg_conn = None

    def _get_psycopg_conn(self):
        """Get or create a raw psycopg connection for direct SQL."""
        if self._psycopg_conn is None or self._psycopg_conn.closed:
            self._psycopg_conn = psycopg.connect(self._connection_string, autocommit=True)
        return self._psycopg_conn

    @property
    def llama_store(self) -> "PGVectorStore":
        """LlamaIndex VectorStore for the default collection."""
        return self.get_llama_store(self._default_collection)

    def get_llama_store(self, collection_name: str) -> "PGVectorStore":
        """Get a LlamaIndex VectorStore for a specific collection.

        Creates the table via PGVectorStore.from_params if not cached.
        LlamaIndex prepends 'data_' to the table_name internally.
        """
        if collection_name not in self._stores:
            # Build both sync (psycopg2) and async (asyncpg) connection strings.
            # from_params builds the async string from individual host/port/etc params
            # which are None when we pass a connection_string, so we must provide both.
            sync_url = self._connection_string.replace(
                "postgresql://", "postgresql+psycopg2://"
            )
            async_url = self._connection_string.replace(
                "postgresql://", "postgresql+asyncpg://"
            )
            self._stores[collection_name] = PGVectorStore.from_params(
                connection_string=sync_url,
                async_connection_string=async_url,
                table_name=collection_name,
                embed_dim=self._embed_dim,
                use_jsonb=True,
                perform_setup=True,
                # Stores are cached for the process lifetime; without pre-ping
                # a Postgres restart leaves every pooled connection dead and
                # the first insert afterwards fails with "SSL connection has
                # been closed unexpectedly" (librarian's own engine in db.py
                # already pre-pings).
                create_engine_kwargs={"pool_pre_ping": True},
            )
        return self._stores[collection_name]

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection's data table exists.

        LlamaIndex creates tables as 'data_{collection_name}',
        so we check for that naming convention.
        """
        conn = self._get_psycopg_conn()
        cur = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' "
            "    AND table_name = %s"
            ")",
            (f"data_{collection_name}",),
        )
        return cur.fetchone()[0]

    def get_indexed_ids(self, collection_name: str, id_field: str = "book_id") -> set[int]:
        """Get unique values of an ID field from a collection.

        Queries the metadata_ JSONB column directly via SQL.
        """
        if not self.collection_exists(collection_name):
            return set()

        conn = self._get_psycopg_conn()
        table = f"data_{collection_name}"
        cur = conn.execute(
            f"SELECT DISTINCT (metadata_->>%s)::int FROM {table} "
            f"WHERE metadata_->>%s IS NOT NULL",
            (id_field, id_field),
        )
        return {row[0] for row in cur.fetchall()}

    def delete_by_filter(self, collection_name: str, field: str, value: int) -> None:
        """Delete documents where a metadata field equals value.

        Direct SQL delete against the metadata_ JSONB column.
        """
        if not self.collection_exists(collection_name):
            return

        conn = self._get_psycopg_conn()
        table = f"data_{collection_name}"
        conn.execute(
            f"DELETE FROM {table} WHERE metadata_->>%s = %s",
            (field, str(value)),
        )

    def get_collection_count(self, collection_name: str) -> int:
        """Get number of documents in a collection."""
        if not self.collection_exists(collection_name):
            return 0

        conn = self._get_psycopg_conn()
        table = f"data_{collection_name}"
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]

    def get_metadata_counts(self, collection_name: str, field: str) -> dict[str, int]:
        """Count occurrences of each distinct value for a metadata field."""
        if not self.collection_exists(collection_name):
            return {}

        conn = self._get_psycopg_conn()
        table = f"data_{collection_name}"
        cur = conn.execute(
            f"SELECT COALESCE(metadata_->>%s, 'Unknown'), COUNT(*) "
            f"FROM {table} GROUP BY 1 ORDER BY 2 DESC",
            (field,),
        )
        return {row[0]: row[1] for row in cur.fetchall()}

    def get_documents_by_filter(
        self, collection_name: str, filters: dict[str, any]
    ) -> list[tuple[str, dict]]:
        """Get documents matching metadata filters."""
        if not self.collection_exists(collection_name):
            return []

        conn = self._get_psycopg_conn()
        table = f"data_{collection_name}"

        conditions = []
        params = []
        for k, v in filters.items():
            conditions.append("metadata_->>%s = %s")
            params.extend([k, str(v)])

        where = " AND ".join(conditions)
        cur = conn.execute(
            f"SELECT text, metadata_ FROM {table} WHERE {where}",
            params,
        )
        return [(row[0] or "", dict(row[1]) if row[1] else {}) for row in cur.fetchall()]

    def text_search(
        self,
        collection_name: str,
        query: str,
        book_id: int | None = None,
        library: str | None = None,
        limit: int = 10,
    ) -> list[tuple[str, dict]]:
        """Literal text search using SQL ILIKE.

        Finds chunks containing the exact query string (case-insensitive).
        Useful for part numbers, error codes, and other literal values that
        semantic search can't find.

        Returns list of (text, metadata) tuples.
        """
        if not self.collection_exists(collection_name):
            return []

        conn = self._get_psycopg_conn()
        table = f"data_{collection_name}"

        conditions = ["text ILIKE %s"]
        params: list = [f"%{query}%"]

        if book_id is not None:
            conditions.append("metadata_->>'book_id' = %s")
            params.append(str(book_id))
        if library:
            conditions.append("metadata_->>'library' = %s")
            params.append(library)

        where = " AND ".join(conditions)
        cur = conn.execute(
            f"SELECT text, metadata_ FROM {table} WHERE {where} LIMIT %s",
            params + [limit],
        )
        return [(row[0] or "", dict(row[1]) if row[1] else {}) for row in cur.fetchall()]

    def update_metadata_by_book_id(
        self, collection_name: str, book_id: int, updates: dict[str, str]
    ) -> int:
        """Update metadata fields on all chunks belonging to a book.

        Updates both the top-level metadata_ JSONB payload and the serialized
        LlamaIndex node content, which also carries a copy of node metadata.
        Returns the number of rows updated.
        """
        if not updates or not self.collection_exists(collection_name):
            return 0

        conn = self._get_psycopg_conn()
        table = f"data_{collection_name}"

        cur = conn.execute(
            f"SELECT id, metadata_ FROM {table} "
            f"WHERE metadata_->>'book_id' = %s",
            (str(book_id),),
        )
        rows = cur.fetchall()
        for row_id, metadata in rows:
            conn.execute(
                f"UPDATE {table} SET metadata_ = %s WHERE id = %s",
                (Jsonb(_metadata_with_node_content_updates(dict(metadata or {}), updates)), row_id),
            )
        return len(rows)

    def requires_lock(self) -> bool:
        """PostgreSQL handles concurrency, no external lock needed."""
        return False
