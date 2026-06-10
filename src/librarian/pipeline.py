"""Extraction and indexing pipeline orchestration.

Owns the background workers and their status state machine
(pending → extracting → extracted → indexing → indexed | failed).
The MCP tools in librarian.mcp_server are thin adapters over
start_extraction/start_indexing.
"""

import logging
import threading
import time
from pathlib import Path

from librarian.config import expand_path
from librarian.db import Book, get_session, session_scope

log = logging.getLogger(__name__)


def run_in_background(target, *args) -> threading.Thread:
    """Launch a daemon worker thread. Module-level so tests can monkeypatch."""
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


def update_book_status(
    book_id: int, status: str, message: str | None = None,
    config: dict | None = None, **fields,
):
    """Update book status from a background thread (uses its own session)."""
    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if book:
            book.status = status
            book.status_message = message
            for k, v in fields.items():
                if hasattr(book, k):
                    setattr(book, k, v)
            session.commit()
    except Exception as e:
        session.rollback()
        log.error(f"Failed to update book {book_id} status: {e}")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def start_extraction(config: dict, book_id: int, force: bool = False) -> dict:
    """Validate and launch background extraction for a book."""
    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        if not force and book.status in ("extracting", "extracted", "indexing", "indexed"):
            return {
                "success": False,
                "error": f"Book {book_id} is {book.status}. Use force=True to re-extract.",
            }

        if not book.source_path:
            return {"success": False, "error": f"Book {book_id} has no source_path"}

        source = Path(book.source_path)
        if not source.exists():
            return {"success": False, "error": f"Source file not found: {source}"}

        if source.suffix.lower() not in {".pdf", ".epub"}:
            return {
                "success": False,
                "error": f"Unsupported format {source.suffix}, need PDF or EPUB",
            }

        output_path = expand_path(config["output_path"])
        book_output = output_path / str(book_id)
        book_output.mkdir(parents=True, exist_ok=True)

        book.status = "extracting"
        book.status_message = "Starting extraction..."
        session.commit()

        title = book.title
        source_path = book.source_path

    run_in_background(extract_worker, book_id, source_path, str(book_output), config)

    return {
        "success": True,
        "book_id": book_id,
        "status": "extracting",
        "message": f"Extraction started for '{title}'. Use book_status({book_id}) to track progress.",
    }


def extract_worker(book_id: int, source_path: str, output_dir: str, config: dict):
    """Background worker for book extraction.

    Delegates to librarian.extract.extract, which routes by format
    (PDF → marker + grobid extractors, EPUB → ebooklib) and persists
    metadata.json alongside the artifacts.
    """
    source = Path(source_path)
    book_output = Path(output_dir)
    ext = source.suffix.lower()

    try:
        file_size_mb = source.stat().st_size / (1024 * 1024)

        if ext not in (".pdf", ".epub"):
            update_book_status(
                book_id, "failed",
                f"Unsupported extension {ext} (expected .pdf or .epub)",
                config,
            )
            return

        from librarian.extract import extract

        update_book_status(
            book_id, "extracting",
            f"Extracting {source.name} ({file_size_mb:.1f} MB)...",
            config,
        )
        t0 = time.monotonic()
        result = extract(source, book_output, config)
        extraction_duration = time.monotonic() - t0

        # Without primary content (blocks/markdown) there is nothing to
        # index — fail now rather than confusingly at index time.
        if not result.has_content:
            detail = "; ".join(result.errors) or "no indexable content produced"
            update_book_status(
                book_id, "failed", f"extraction incomplete: {detail}",
                config, extraction_duration_s=extraction_duration,
            )
            return

        message = f"Extraction complete in {extraction_duration:.0f}s"
        if result.errors:
            message += f" (partial: {'; '.join(result.errors)})"

        update_book_status(
            book_id, "extracted", message,
            config,
            converted_path=str(book_output),
            extraction_duration_s=extraction_duration,
        )

        # Record which backend ran (spark/modal for PDFs) so it's visible in
        # book_status without digging through container logs.
        if result.metadata.extraction_backend:
            from librarian.db import update_book_fields

            update_book_fields(
                book_id, config,
                extraction_backend=result.metadata.extraction_backend,
            )
    except Exception as e:
        log.error(f"Extraction failed for book {book_id}: {e}")
        update_book_status(book_id, "failed", str(e), config)


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def start_indexing(config: dict, book_id: int) -> dict:
    """Validate and launch background indexing for an extracted book."""
    output_path = expand_path(config["output_path"])
    book_dir = output_path / str(book_id)

    if not book_dir.exists():
        return {"success": False, "error": f"No extracted content at {book_dir}"}

    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found in database"}

        if book.status == "indexing":
            return {
                "success": False,
                "error": f"Book {book_id} is already being indexed. "
                         f"Use book_status({book_id}) to check progress.",
            }

        book.status = "indexing"
        book.status_message = "Starting indexing..."
        session.commit()

        title = book.title

    run_in_background(index_worker, book_id, config)

    return {
        "success": True,
        "book_id": book_id,
        "status": "indexing",
        "message": f"Indexing started for '{title}'. Use book_status({book_id}) to track progress.",
    }


def index_worker(book_id: int, config: dict):
    """Background worker for book indexing."""
    from llama_index.core import Settings

    from librarian.embeddings import get_embed_model
    from librarian.index import (
        index_book as _index_book_impl,
        load_extracted_blocks,
        load_extracted_book,
    )
    from librarian.vectorstore import get_collection_names, get_vector_store

    Settings.embed_model = get_embed_model(config)

    try:
        output_path = expand_path(config["output_path"])
        book_dir = output_path / str(book_id)

        update_book_status(book_id, "indexing", "Loading extracted content...", config)

        with session_scope(config) as session:
            book = session.query(Book).filter(Book.id == book_id).first()
            if not book:
                update_book_status(book_id, "failed", f"Book {book_id} not found", config)
                return

            metadata = {
                "id": book_id,
                "title": book.title,
                "authors": book.authors or [],
                "subjects": book.subjects or [],
                "tags": [],
                "*library": book.library or "",
                "source_path": book.source_path or "",
            }

        content, raw_content = load_extracted_book(book_dir)
        if not content:
            update_book_status(book_id, "failed", "No extracted markdown found", config)
            return

        blocks = load_extracted_blocks(book_dir)

        update_book_status(book_id, "indexing", "Clearing old entries...", config)

        store = get_vector_store(config)
        collections = get_collection_names(config)
        vector_store = store.get_llama_store(collections["full"])
        equation_store = store.get_llama_store(collections["equations"])
        chapter_store = store.get_llama_store(collections["chapters"])

        for coll in collections.values():
            store.delete_by_filter(coll, "book_id", book_id)

        n_blocks = len(blocks) if blocks else 0
        update_book_status(
            book_id, "indexing",
            f"Embedding ~{n_blocks} blocks (~8 it/s, several minutes)...",
            config,
        )

        def _on_progress(done, total, message):
            update_book_status(book_id, "indexing", message, config)

        chunks, eq_count, ch_count = _index_book_impl(
            book_id, content, raw_content, metadata,
            vector_store, equation_store, chapter_store, config,
            blocks=blocks,
            progress_fn=_on_progress,
        )

        update_book_status(
            book_id, "indexed",
            f"Indexed {chunks} chunks, {eq_count} equations, {ch_count} summaries",
            config,
        )
    except Exception as e:
        log.error(f"Indexing failed for book {book_id}: {e}")
        update_book_status(book_id, "failed", str(e), config)
