"""LanceDB vector store backend.

LanceDB is an embedded vector database with:
- Non-blocking concurrent reads
- Low memory footprint (~4MB idle)
- Good performance for development workloads

Note: LanceDB is an optional dependency. Install with:
    pip install -e ".[lancedb]"
"""

from pathlib import Path

try:
    import lancedb
    from llama_index.vector_stores.lancedb import LanceDBVectorStore

    LANCEDB_AVAILABLE = True
except ImportError:
    LANCEDB_AVAILABLE = False
    lancedb = None
    LanceDBVectorStore = None


class LanceDBStore:
    """LanceDB vector store backend.

    Embedded database suitable for development. Supports concurrent reads
    without external locking. Tables map to collections.
    """

    def __init__(self, uri: Path | str, default_collection: str = "librarian_full"):
        """Initialize LanceDB store.

        Args:
            uri: Path to LanceDB storage directory
            default_collection: Default table name for llama_store property

        Raises:
            ImportError: If lancedb is not installed
        """
        if not LANCEDB_AVAILABLE:
            raise ImportError(
                "LanceDB is not installed. Install with: pip install -e '.[lancedb]'"
            )

        self._uri = Path(uri) if isinstance(uri, str) else uri
        self._default_collection = default_collection
        self._db: "lancedb.DBConnection" | None = None
        self._stores: dict[str, "LanceDBVectorStore"] = {}

        # Ensure directory exists
        self._uri.mkdir(parents=True, exist_ok=True)

    @property
    def db(self) -> "lancedb.DBConnection":
        """Lazy-initialize LanceDB connection."""
        if self._db is None:
            self._db = lancedb.connect(str(self._uri))
        return self._db

    @property
    def llama_store(self) -> "LanceDBVectorStore":
        """LlamaIndex VectorStore for the default collection."""
        return self.get_llama_store(self._default_collection)

    def get_llama_store(self, collection_name: str) -> "LanceDBVectorStore":
        """Get a LlamaIndex VectorStore for a specific collection (table).

        Creates the table if it doesn't exist (handled by LanceDBVectorStore).
        """
        if collection_name not in self._stores:
            self._stores[collection_name] = LanceDBVectorStore(
                uri=str(self._uri),
                table_name=collection_name,
            )
        return self._stores[collection_name]

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a table exists in LanceDB."""
        try:
            return collection_name in self.db.table_names()
        except Exception:
            return False

    def get_indexed_ids(self, collection_name: str, id_field: str = "book_id") -> set[int]:
        """Get unique values of an ID field from a table.

        Uses LanceDB's native query interface for efficient ID extraction.
        """
        try:
            if not self.collection_exists(collection_name):
                return set()

            table = self.db.open_table(collection_name)

            # LanceDB tables store data as Arrow tables
            # Query all rows but only select the ID field
            # The field is in the metadata column for LlamaIndex
            df = table.to_pandas()

            # LlamaIndex stores metadata in a 'metadata' column as JSON
            # We need to extract book_id from it
            indexed = set()
            if "metadata" in df.columns:
                for metadata in df["metadata"]:
                    if metadata and isinstance(metadata, dict):
                        if id_field in metadata:
                            indexed.add(metadata[id_field])
                    elif metadata and isinstance(metadata, str):
                        import json

                        try:
                            meta_dict = json.loads(metadata)
                            if id_field in meta_dict:
                                indexed.add(meta_dict[id_field])
                        except (json.JSONDecodeError, TypeError):
                            pass

            return indexed
        except Exception:
            return set()

    def delete_by_filter(self, collection_name: str, field: str, value: int) -> None:
        """Delete documents where field equals value.

        LanceDB uses SQL-like WHERE clauses for deletion.
        """
        try:
            if not self.collection_exists(collection_name):
                return

            table = self.db.open_table(collection_name)

            # LanceDB delete uses SQL-like predicates
            # For metadata fields, we need to use json_extract or similar
            # LlamaIndex LanceDB stores metadata as a struct or JSON column
            # The exact predicate depends on how metadata is stored

            # Option 1: If metadata is a JSON string column
            # table.delete(f"json_extract(metadata, '$.{field}') = {value}")

            # Option 2: If metadata is a struct column (more common in LanceDB)
            # We'll try the struct access pattern first
            try:
                table.delete(f"`metadata`.`{field}` = {value}")
            except Exception:
                # Fallback: read all, filter, rewrite
                # This is less efficient but guaranteed to work
                df = table.to_pandas()
                if "metadata" in df.columns:
                    keep_mask = []
                    for metadata in df["metadata"]:
                        if metadata and isinstance(metadata, dict):
                            keep_mask.append(metadata.get(field) != value)
                        else:
                            keep_mask.append(True)
                    # Rewrite table without deleted rows
                    if not all(keep_mask):
                        df_filtered = df[keep_mask]
                        # Drop and recreate table
                        self.db.drop_table(collection_name)
                        if len(df_filtered) > 0:
                            self.db.create_table(collection_name, df_filtered)
        except Exception:
            pass

    def get_collection_count(self, collection_name: str) -> int:
        """Get number of documents in a table."""
        try:
            if not self.collection_exists(collection_name):
                return 0
            table = self.db.open_table(collection_name)
            return table.count_rows()
        except Exception:
            return 0

    def get_metadata_counts(self, collection_name: str, field: str) -> dict[str, int]:
        """Count occurrences of each distinct value for a metadata field."""
        try:
            if not self.collection_exists(collection_name):
                return {}

            table = self.db.open_table(collection_name)
            df = table.to_pandas()
            counts: dict[str, int] = {}

            if "metadata" in df.columns:
                for metadata in df["metadata"]:
                    if metadata and isinstance(metadata, dict):
                        val = str(metadata.get(field, "Unknown"))
                    elif metadata and isinstance(metadata, str):
                        import json

                        try:
                            meta_dict = json.loads(metadata)
                            val = str(meta_dict.get(field, "Unknown"))
                        except (json.JSONDecodeError, TypeError):
                            val = "Unknown"
                    else:
                        val = "Unknown"
                    counts[val] = counts.get(val, 0) + 1

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

            table = self.db.open_table(collection_name)
            df = table.to_pandas()
            results = []

            if "metadata" not in df.columns or "text" not in df.columns:
                return []

            for _, row in df.iterrows():
                metadata = row["metadata"]
                if metadata and isinstance(metadata, str):
                    import json

                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        continue

                if not metadata or not isinstance(metadata, dict):
                    continue

                if all(metadata.get(k) == v for k, v in filters.items()):
                    results.append((row.get("text", ""), dict(metadata)))

            return results
        except Exception:
            return []

    def update_metadata_by_book_id(
        self, collection_name: str, book_id: int, updates: dict[str, str]
    ) -> int:
        """Not implemented for LanceDB (dev backend; production uses pgvector)."""
        raise NotImplementedError(
            "update_metadata_by_book_id is not implemented for LanceDBStore"
        )

    def requires_lock(self) -> bool:
        """LanceDB handles concurrent reads without external locking."""
        return False
