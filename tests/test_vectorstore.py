"""Tests for vectorstore backends."""

import socket
import tempfile
from pathlib import Path

import pytest

from librarian.vectorstore import get_vector_store, get_collection_names
from librarian.vectorstore.protocol import LibrarianVectorStore
from librarian.vectorstore.qdrant_file import QdrantFileStore


def _pg_reachable(host="agents.local", port=5432, timeout=2) -> bool:
    """Check if PostgreSQL is reachable via TCP."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class TestQdrantFileStore:
    """Tests for QdrantFileStore backend."""

    @pytest.fixture
    def temp_store(self):
        """Create a temporary QdrantFileStore for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = QdrantFileStore(
                path=Path(tmpdir),
                default_collection="test_collection",
            )
            yield store

    def test_implements_protocol(self, temp_store):
        """Store should implement LibrarianVectorStore protocol."""
        assert isinstance(temp_store, LibrarianVectorStore)

    def test_requires_lock(self, temp_store):
        """File-based store should require external locking."""
        assert temp_store.requires_lock() is True

    def test_collection_not_exists(self, temp_store):
        """Non-existent collection should return False."""
        assert temp_store.collection_exists("nonexistent") is False

    def test_get_indexed_ids_empty(self, temp_store):
        """Empty collection should return empty set."""
        result = temp_store.get_indexed_ids("nonexistent")
        assert result == set()

    def test_llama_store_property(self, temp_store):
        """llama_store should return QdrantVectorStore."""
        store = temp_store.llama_store
        assert store is not None
        assert hasattr(store, "add")

    def test_get_llama_store_caching(self, temp_store):
        """get_llama_store should cache stores."""
        store1 = temp_store.get_llama_store("test")
        store2 = temp_store.get_llama_store("test")
        assert store1 is store2


class TestFactory:
    """Tests for get_vector_store factory function."""

    def test_factory_qdrant_file(self):
        """Factory should create QdrantFileStore for qdrant-file backend."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "vector_store": {
                    "backend": "qdrant-file",
                    "qdrant_path": tmpdir,
                    "collection": "test_collection",
                }
            }
            store = get_vector_store(config)
            assert isinstance(store, QdrantFileStore)
            assert store.requires_lock() is True

    def test_factory_default_backend(self):
        """Factory should default to qdrant-file if no backend specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "vector_store": {
                    "qdrant_path": tmpdir,
                }
            }
            store = get_vector_store(config)
            assert isinstance(store, QdrantFileStore)

    def test_factory_unknown_backend(self):
        """Factory should raise ValueError for unknown backend."""
        config = {
            "vector_store": {
                "backend": "unknown",
            }
        }
        with pytest.raises(ValueError, match="Unknown vector store backend"):
            get_vector_store(config)

    def test_factory_with_default_collection(self):
        """Factory should respect default_collection override."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "vector_store": {
                    "backend": "qdrant-file",
                    "qdrant_path": tmpdir,
                    "collection": "config_collection",
                }
            }
            store = get_vector_store(config, default_collection="override_collection")
            # Check that llama_store uses the override collection
            assert store._default_collection == "override_collection"


class TestGetCollectionNames:
    """Tests for get_collection_names helper."""

    def test_default_names(self):
        """Should return default collection names."""
        config = {"vector_store": {}}
        names = get_collection_names(config)
        assert names == {
            "full": "librarian_full",
            "equations": "librarian_equations",
            "chapters": "librarian_chapters",
        }

    def test_custom_names(self):
        """Should return custom collection names from config."""
        config = {
            "vector_store": {
                "collection": "my_full",
                "equation_collection": "my_equations",
                "chapter_collection": "my_chapters",
            }
        }
        names = get_collection_names(config)
        assert names == {
            "full": "my_full",
            "equations": "my_equations",
            "chapters": "my_chapters",
        }


class TestLanceDBStore:
    """Tests for LanceDBStore backend (requires lancedb installed)."""

    @pytest.fixture
    def temp_lancedb_store(self):
        """Create a temporary LanceDBStore for testing."""
        try:
            from librarian.vectorstore.lancedb_store import LanceDBStore, LANCEDB_AVAILABLE
        except ImportError:
            pytest.skip("lancedb not installed")
            return

        if not LANCEDB_AVAILABLE:
            pytest.skip("lancedb not installed")
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            store = LanceDBStore(
                uri=Path(tmpdir),
                default_collection="test_collection",
            )
            yield store

    def test_implements_protocol(self, temp_lancedb_store):
        """Store should implement LibrarianVectorStore protocol."""
        assert isinstance(temp_lancedb_store, LibrarianVectorStore)

    def test_requires_lock(self, temp_lancedb_store):
        """LanceDB should not require external locking."""
        assert temp_lancedb_store.requires_lock() is False

    def test_collection_not_exists(self, temp_lancedb_store):
        """Non-existent collection should return False."""
        assert temp_lancedb_store.collection_exists("nonexistent") is False


class TestQdrantServerStore:
    """Tests for QdrantServerStore backend."""

    def test_requires_lock(self):
        """Server store should not require external locking."""
        from librarian.vectorstore.qdrant_server import QdrantServerStore

        store = QdrantServerStore(host="localhost", port=6333)
        assert store.requires_lock() is False

    def test_implements_protocol(self):
        """Store should implement LibrarianVectorStore protocol."""
        from librarian.vectorstore.qdrant_server import QdrantServerStore

        store = QdrantServerStore(host="localhost", port=6333)
        assert isinstance(store, LibrarianVectorStore)


class TestChromaStore:
    """Tests for ChromaStore backend (requires chromadb installed)."""

    @pytest.fixture
    def temp_chroma_store(self):
        """Create a temporary ChromaStore for testing."""
        try:
            from librarian.vectorstore.chroma_store import ChromaStore, CHROMA_AVAILABLE
        except ImportError:
            pytest.skip("chromadb not installed")
            return

        if not CHROMA_AVAILABLE:
            pytest.skip("chromadb not installed")
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ChromaStore(
                path=Path(tmpdir),
                default_collection="test_collection",
            )
            yield store

    def test_implements_protocol(self, temp_chroma_store):
        """Store should implement LibrarianVectorStore protocol."""
        assert isinstance(temp_chroma_store, LibrarianVectorStore)

    def test_requires_lock(self, temp_chroma_store):
        """Chroma should not require external locking."""
        assert temp_chroma_store.requires_lock() is False

    def test_collection_not_exists(self, temp_chroma_store):
        """Non-existent collection should return False."""
        assert temp_chroma_store.collection_exists("nonexistent") is False

    def test_get_indexed_ids_empty(self, temp_chroma_store):
        """Empty/non-existent collection should return empty set."""
        result = temp_chroma_store.get_indexed_ids("nonexistent")
        assert result == set()

    def test_llama_store_property(self, temp_chroma_store):
        """llama_store should return ChromaVectorStore."""
        store = temp_chroma_store.llama_store
        assert store is not None
        assert hasattr(store, "add")

    def test_get_llama_store_caching(self, temp_chroma_store):
        """get_llama_store should cache stores."""
        store1 = temp_chroma_store.get_llama_store("test")
        store2 = temp_chroma_store.get_llama_store("test")
        assert store1 is store2

    def test_collection_exists_after_create(self, temp_chroma_store):
        """Collection should exist after get_llama_store creates it."""
        temp_chroma_store.get_llama_store("new_collection")
        assert temp_chroma_store.collection_exists("new_collection") is True


@pytest.mark.skipif(not _pg_reachable(), reason="PostgreSQL on agents.local not reachable")
class TestPgvectorStore:
    """Tests for PgvectorStore backend (requires PostgreSQL with pgvector)."""

    @pytest.fixture
    def pg_store(self):
        """Create a PgvectorStore pointing at agents.local."""
        try:
            from librarian.vectorstore.pgvector_store import PgvectorStore, PGVECTOR_AVAILABLE
        except ImportError:
            pytest.skip("pgvector dependencies not installed")
            return

        if not PGVECTOR_AVAILABLE:
            pytest.skip("llama-index-vector-stores-postgres not installed")
            return

        store = PgvectorStore(
            connection_string="postgresql://dmichael@agents.local:5432/librarian",
            embed_dim=768,
            default_collection="test_collection",
        )
        yield store

    def test_implements_protocol(self, pg_store):
        """Store should implement LibrarianVectorStore protocol."""
        assert isinstance(pg_store, LibrarianVectorStore)

    def test_requires_lock(self, pg_store):
        """pgvector should not require external locking."""
        assert pg_store.requires_lock() is False

    def test_collection_not_exists(self, pg_store):
        """Non-existent collection should return False."""
        assert pg_store.collection_exists("nonexistent_table_xyz") is False

    def test_get_indexed_ids_empty(self, pg_store):
        """Non-existent collection should return empty set."""
        result = pg_store.get_indexed_ids("nonexistent_table_xyz")
        assert result == set()
