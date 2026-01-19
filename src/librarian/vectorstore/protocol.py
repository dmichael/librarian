"""Protocol for Librarian vector store backends.

This module defines the interface that all vector store backends must implement.
The abstraction wraps LlamaIndex's vector store while adding Librarian-specific
operations like collection existence checks, ID retrieval, and filter-based deletion.
"""

from typing import Protocol, runtime_checkable

from llama_index.core.vector_stores.types import BasePydanticVectorStore


@runtime_checkable
class LibrarianVectorStore(Protocol):
    """Protocol for Librarian vector store backends.

    Each backend must provide:
    - A LlamaIndex-compatible vector store for standard operations
    - Collection management (existence checks)
    - ID retrieval for tracking indexed books
    - Filter-based deletion for re-indexing
    - Lock requirement indicator for concurrent access
    """

    @property
    def llama_store(self) -> BasePydanticVectorStore:
        """LlamaIndex VectorStore for standard operations (add, query)."""
        ...

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection/table exists.

        Args:
            collection_name: Name of the collection to check

        Returns:
            True if collection exists, False otherwise
        """
        ...

    def get_indexed_ids(self, collection_name: str, id_field: str = "book_id") -> set[int]:
        """Get unique values of an ID field (for tracking indexed books).

        Scrolls through all documents in a collection and extracts unique
        values of the specified field.

        Args:
            collection_name: Name of the collection to query
            id_field: Name of the metadata field containing IDs

        Returns:
            Set of unique integer IDs found in the collection
        """
        ...

    def delete_by_filter(self, collection_name: str, field: str, value: int) -> None:
        """Delete documents where field equals value (for --force re-indexing).

        Args:
            collection_name: Name of the collection to delete from
            field: Metadata field to filter on
            value: Value to match for deletion
        """
        ...

    def requires_lock(self) -> bool:
        """Whether this backend needs external locking for concurrent access.

        Returns:
            True if backend requires file locking (e.g., Qdrant file-based),
            False if backend handles concurrency internally (e.g., server, LanceDB)
        """
        ...

    def get_llama_store(self, collection_name: str) -> BasePydanticVectorStore:
        """Get a LlamaIndex VectorStore for a specific collection.

        Args:
            collection_name: Name of the collection

        Returns:
            LlamaIndex VectorStore instance for the collection
        """
        ...
