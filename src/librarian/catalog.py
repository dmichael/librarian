"""Books-table catalog operations.

Business logic shared by the MCP tools and HTTP routes in
librarian.mcp_server. Functions take a config dict, manage their own
sessions, and return plain dicts. Domain failures (missing book, duplicate)
are returned as result dicts; unexpected errors raise and are converted to
error envelopes by the serving layer.
"""

import logging
from pathlib import Path

from librarian.db import Book, book_to_dict, session_scope
from librarian.metadata_types import (
    META_AUTHORS,
    META_LIBRARY,
    META_SUBJECTS,
    META_TITLE,
    serialize_list_metadata,
)

log = logging.getLogger(__name__)


def build_vector_metadata_updates(
    *,
    title: str | None = None,
    authors: list[str] | None = None,
    subjects: list[str] | None = None,
    library: str | None = None,
) -> dict[str, str]:
    """Build vector metadata updates for mutable book fields."""
    vector_updates = {}
    if title is not None:
        vector_updates[META_TITLE] = title
    if authors is not None:
        vector_updates[META_AUTHORS] = ", ".join(authors)
    if library is not None:
        vector_updates[META_LIBRARY] = library
    if subjects is not None:
        vector_updates[META_SUBJECTS] = serialize_list_metadata(subjects)
    return vector_updates


def next_pipeline_steps(book_id: int) -> list[str]:
    """The standard 'what to do after registering a book' checklist."""
    return [
        f"extract_book(book_id={book_id}) — extract to searchable text",
        f"index_book(book_id={book_id}) — embed and store in vector search",
        f"suggest_tags(book_id={book_id}) — get subject/library tag suggestions",
        f"update_book(book_id={book_id}, subjects=..., library=...) — apply tags",
    ]


def public_base_url(config: dict) -> str:
    """Base URL clients should use to reach the HTTP endpoints."""
    base = config.get("public_url")
    if not base:
        host = config.get("host", "localhost")
        port = config.get("port", 8811)
        base = f"http://{host}:{port}"
    return base.rstrip("/")


def register_book(
    config: dict,
    *,
    title: str,
    authors: list[str] | None = None,
    format: str = "pdf",
    source_path: str | None = None,
) -> dict:
    """Create a book row, deduplicating by title then source_path.

    Returns the existing record (with already_exists=True) when a book with
    the same title or source path is already registered.
    """
    with session_scope(config) as session:
        existing = session.query(Book).filter(Book.title.ilike(title)).first()
        if not existing and source_path:
            existing = session.query(Book).filter(
                Book.source_path == source_path
            ).first()
        if existing:
            return {
                "success": True,
                "book_id": existing.id,
                "title": existing.title,
                "status": existing.status,
                "already_exists": True,
            }

        book = Book(
            title=title,
            authors=authors or [],
            format=format,
            source_path=source_path,
            status="pending",
        )
        session.add(book)
        session.flush()  # assign book.id before building next_steps

        return {
            "success": True,
            "book_id": book.id,
            "title": book.title,
            "status": book.status,
            "next_steps": next_pipeline_steps(book.id),
        }


def list_books(config: dict, status: str | None = None) -> list[dict]:
    """All books (optionally filtered by status) as plain dicts."""
    with session_scope(config) as session:
        q = session.query(Book)
        if status:
            q = q.filter(Book.status == status)
        return [book_to_dict(b) for b in q.order_by(Book.id).all()]


def get_book_status(config: dict, book_id: int) -> dict:
    """Status and metadata for one book."""
    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        row = book_to_dict(book)
        row.update({
            "success": True,
            "status_message": book.status_message,
            "extraction_duration_s": book.extraction_duration_s,
            "created_at": str(book.created_at) if book.created_at else None,
            "updated_at": str(book.updated_at) if book.updated_at else None,
        })
        return row


def update_book(
    config: dict,
    book_id: int,
    *,
    title: str | None = None,
    authors: list[str] | None = None,
    subjects: list[str] | None = None,
    library: str | None = None,
) -> dict:
    """Update book metadata and propagate display/filter fields to vectors."""
    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        if title is not None:
            book.title = title
        if authors is not None:
            book.authors = authors
        if subjects is not None:
            book.subjects = subjects
        if library is not None:
            book.library = library

        session.commit()

        # Propagate mutable display/filter metadata to vector chunks so
        # retrieval results stay aligned with the canonical books row.
        vector_updates = build_vector_metadata_updates(
            title=title, authors=authors, subjects=subjects, library=library,
        )

        chunks_updated = 0
        if vector_updates:
            try:
                from librarian.vectorstore import get_collection_names, get_vector_store

                store = get_vector_store(config)
                collections = get_collection_names(config)
                for coll in collections.values():
                    chunks_updated += store.update_metadata_by_book_id(
                        coll, book_id, vector_updates
                    )
            except Exception as e:
                log.warning("Failed to propagate metadata to vectors: %s", e)

        return {
            "success": True,
            "book_id": book.id,
            "title": book.title,
            "authors": book.authors or [],
            "subjects": book.subjects or [],
            "library": book.library,
            "chunks_updated": chunks_updated,
        }


def delete_book(config: dict, book_id: int) -> dict:
    """Delete a book row and its vectors (source/extracted files stay on disk)."""
    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        title = book.title

        from librarian.vectorstore import get_collection_names, get_vector_store

        store = get_vector_store(config)
        collections = get_collection_names(config)
        for coll in collections.values():
            store.delete_by_filter(coll, "book_id", book_id)

        session.delete(book)

        return {
            "success": True,
            "book_id": book_id,
            "title": title,
            "deleted": True,
        }


def library_profile(config: dict) -> dict:
    """Oriented summary of library state for agent onboarding."""
    with session_scope(config) as session:
        books = session.query(Book).order_by(Book.id).all()

        all_subjects = set()
        all_libraries = set()
        by_library = {}
        by_status = {}
        untagged = []

        for b in books:
            status = b.status or "unknown"
            by_status[status] = by_status.get(status, 0) + 1

            lib = b.library or "unassigned"
            all_libraries.add(lib)
            by_library.setdefault(lib, []).append({
                "id": b.id,
                "title": b.title,
                "authors": b.authors or [],
                "status": b.status,
                "subjects": b.subjects or [],
            })

            if b.subjects:
                all_subjects.update(b.subjects)
            else:
                untagged.append({"id": b.id, "title": b.title})

        total_books = len(books)

    # Chunk counts per book from the vector store (outside the session)
    chunk_counts = {}
    try:
        from librarian.vectorstore import get_collection_names, get_vector_store

        store = get_vector_store(config)
        collections = get_collection_names(config)
        raw = store.get_metadata_counts(collections["full"], "book_id")
        chunk_counts = {int(k): v for k, v in raw.items()}
    except Exception as e:
        log.warning("Failed to load chunk counts: %s", e)

    collections_summary = {}
    for lib, lib_books in by_library.items():
        indexed = [b for b in lib_books if b["status"] == "indexed"]
        collections_summary[lib] = {
            "total": len(lib_books),
            "indexed": len(indexed),
            "books": [
                {
                    "id": b["id"],
                    "title": b["title"],
                    "authors": b["authors"],
                    "chunks": chunk_counts.get(b["id"], 0),
                    "subjects": b["subjects"],
                }
                for b in indexed
            ],
        }

    return {
        "summary": {
            "total_books": total_books,
            "by_status": by_status,
        },
        "subjects_in_use": sorted(all_subjects),
        "libraries_in_use": sorted(all_libraries - {"unassigned"}),
        "collections": collections_summary,
        "books_without_subjects": untagged,
        "hint": (
            "Use update_book to tag books with subjects and library. "
            "Use search with subjects=['therapy/dbt'] or library='therapy-core' to filter. "
            "Call upload_book() to get the HTTP upload endpoint and curl example for adding new books."
        ),
    }


def download_info(config: dict, book_id: int) -> dict:
    """Download URL and file facts for a book's source file."""
    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}
        if not book.source_path:
            return {"success": False, "error": f"Book {book_id} has no source file"}
        source = Path(book.source_path)
        if not source.exists():
            return {"success": False, "error": f"Source file not found on disk: {source.name}"}

        return {
            "success": True,
            "book_id": book_id,
            "title": book.title,
            "format": book.format,
            "download_url": f"{public_base_url(config)}/download/{book_id}",
            "size_bytes": source.stat().st_size,
        }


def source_file_for_download(config: dict, book_id: int) -> tuple[Path | None, str | None, str | None]:
    """Resolve a book's source file for the HTTP download route.

    Returns (path, download_filename, error). Exactly one of path/error is set.
    """
    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return None, None, f"Book {book_id} not found"
        if not book.source_path:
            return None, None, f"Book {book_id} has no source file"
        source = Path(book.source_path)
        if not source.exists():
            return None, None, f"Source file not found on disk: {source.name}"
        title_slug = book.title[:60].replace(" ", "_").replace("/", "-")
        return source, f"{title_slug}{source.suffix}", None
