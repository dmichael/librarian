"""Librarian MCP server.

Exposes the full librarian pipeline as MCP tools over streamable HTTP:
  search         — semantic search across indexed books
  index_book     — embed and store chunks for an extracted book
  extract_book   — extract PDF → markdown via Modal cloud GPUs
  ingest_book    — register a new book in the books table
  list_books     — list books with metadata/status
  book_status    — pipeline statistics (counts by status, chunk counts)

Run:
    python -m librarian.mcp_server          # default: 0.0.0.0:8811
    librarian-serve                         # via entry point
"""

import logging
import sys
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from librarian.config import load_config
from librarian.db import Book, get_session
from librarian.metadata_types import (
    META_BOOK_ID,
    META_LIBRARY,
    META_SUBJECTS,
    build_search_result_row,
    build_text_search_result_row,
    serialize_list_metadata,
)

log = logging.getLogger(__name__)

mcp = FastMCP("librarian", host="0.0.0.0", port=8811)

# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------

def _update_book_status(book_id: int, status: str, message: str = None, **kwargs):
    """Update book status from a background thread (uses its own session)."""
    config = _get_config()
    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if book:
            book.status = status
            book.status_message = message
            for k, v in kwargs.items():
                if hasattr(book, k):
                    setattr(book, k, v)
            session.commit()
    except Exception as e:
        session.rollback()
        log.error(f"Failed to update book {book_id} status: {e}")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Lazy singletons — heavy objects created once on first use
# ---------------------------------------------------------------------------

_config = None
_embed_model = None
_embed_lock = threading.Lock()


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_embed_model():
    """Load embedding model once (takes a few seconds on first call).

    Thread-safe — concurrent callers block on the lock while the model loads.
    """
    global _embed_model
    if _embed_model is not None:
        return _embed_model

    with _embed_lock:
        # Double-check after acquiring lock
        if _embed_model is not None:
            return _embed_model

        from llama_index.core import Settings
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding

        config = _get_config()
        emb = config.get("embedding", {})
        model_name = emb.get("model", "BAAI/bge-base-en-v1.5")
        device = emb.get("device", "cpu")

        if "bge" in model_name.lower():
            _embed_model = HuggingFaceEmbedding(
                model_name=model_name,
                device=device,
                query_instruction="Represent this sentence for searching relevant passages: ",
            )
        else:
            _embed_model = HuggingFaceEmbedding(model_name=model_name, device=device)

        Settings.embed_model = _embed_model
    return _embed_model


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search(
    query: str,
    top_k: int = 5,
    book_id: int | None = None,
    library: str | None = None,
    subjects: list[str] | None = None,
) -> list[dict]:
    """Search the library. Returns ranked passages with title, page, chapter, and score.

    Args:
        query: Natural language search query
        top_k: Number of results to return (default 5)
        book_id: Restrict search to a specific book ID
        library: Restrict search to a named library
        subjects: Filter by subject tags (e.g. ["psychology/*"])
    """
    from librarian.query import retrieve

    # Ensure embedding model is loaded
    _get_embed_model()

    config = _get_config()

    # book_id filter uses metadata filter on the retriever
    nodes = retrieve(
        query,
        config=config,
        top_k=top_k,
        subjects=subjects,
        library=library,
    )

    # If book_id filter requested, apply post-hoc (pgvector metadata filter)
    if book_id is not None:
        nodes = [n for n in nodes if n.metadata.get(META_BOOK_ID) == book_id]

    results = []
    for node in nodes:
        results.append(
            build_search_result_row(
                text=node.text,
                score=node.score,
                metadata=node.metadata,
            )
        )

    return results


@mcp.tool()
def text_search(
    query: str,
    book_id: int | None = None,
    library: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Literal text search — finds chunks containing an exact string.

    Use this for part numbers, error codes, specific values, or any query
    where you need an exact substring match rather than semantic similarity.
    Case-insensitive.

    Args:
        query: Exact text to search for (case-insensitive substring match)
        book_id: Restrict to a specific book ID
        library: Restrict to a named library
        limit: Maximum results to return (default 10)
    """
    from librarian.vectorstore import get_collection_names, get_vector_store

    config = _get_config()
    store = get_vector_store(config)
    collections = get_collection_names(config)

    rows = store.text_search(
        collections["full"],
        query,
        book_id=book_id,
        library=library,
        limit=limit,
    )

    results = []
    for text, meta in rows:
        results.append(build_text_search_result_row(text=text, metadata=meta))

    return results


def _index_book_worker(book_id: int):
    """Background worker for book indexing."""
    from llama_index.core import Settings

    from librarian.config import expand_path
    from librarian.index import (
        index_book as _index_book_impl,
        load_extracted_blocks,
        load_extracted_book,
    )
    from librarian.vectorstore import get_collection_names, get_vector_store

    config = _get_config()
    embed_model = _get_embed_model()
    Settings.embed_model = embed_model

    try:
        output_path = expand_path(config["output_path"])
        book_dir = output_path / str(book_id)

        _update_book_status(book_id, "indexing", "Loading extracted content...")

        # Get book metadata
        session = get_session(config)
        try:
            book = session.query(Book).filter(Book.id == book_id).first()
            if not book:
                _update_book_status(book_id, "failed", f"Book {book_id} not found")
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
        finally:
            session.close()

        content, raw_content = load_extracted_book(book_dir)
        if not content:
            _update_book_status(book_id, "failed", "No extracted markdown found")
            return

        blocks = load_extracted_blocks(book_dir)

        _update_book_status(book_id, "indexing", "Clearing old entries...")

        store = get_vector_store(config)
        collections = get_collection_names(config)
        vector_store = store.get_llama_store(collections["full"])
        equation_store = store.get_llama_store(collections["equations"])
        chapter_store = store.get_llama_store(collections["chapters"])

        for coll in [collections["full"], collections["equations"], collections["chapters"]]:
            store.delete_by_filter(coll, "book_id", book_id)

        n_blocks = len(blocks) if blocks else 0
        _update_book_status(
            book_id, "indexing",
            f"Embedding ~{n_blocks} blocks (~8 it/s, several minutes)...",
        )

        def _on_progress(done, total, message):
            _update_book_status(book_id, "indexing", message)

        chunks, eq_count, ch_count = _index_book_impl(
            book_id, content, raw_content, metadata,
            vector_store, equation_store, chapter_store, config,
            blocks=blocks,
            progress_fn=_on_progress,
        )

        _update_book_status(
            book_id, "indexed",
            f"Indexed {chunks} chunks, {eq_count} equations, {ch_count} chapters",
        )
    except Exception as e:
        log.error(f"Indexing failed for book {book_id}: {e}")
        _update_book_status(book_id, "failed", str(e))


@mcp.tool()
def index_book(book_id: int) -> dict:
    """Index an extracted book — embed chunks and store in pgvector.

    Launches indexing in the background and returns immediately.
    Use book_status(book_id) to track progress.

    Args:
        book_id: ID of the book to index
    """
    from librarian.config import expand_path

    config = _get_config()
    output_path = expand_path(config["output_path"])
    book_dir = output_path / str(book_id)

    if not book_dir.exists():
        return {"success": False, "error": f"No extracted content at {book_dir}"}

    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found in database"}

        if book.status == "indexing":
            return {
                "success": False,
                "error": f"Book {book_id} is already being indexed. Use book_status({book_id}) to check progress.",
            }

        # Mark as indexing and launch background worker
        book.status = "indexing"
        book.status_message = "Starting indexing..."
        session.commit()

        thread = threading.Thread(
            target=_index_book_worker,
            args=(book_id,),
            daemon=True,
        )
        thread.start()

        return {
            "success": True,
            "book_id": book_id,
            "status": "indexing",
            "message": f"Indexing started for '{book.title}'. Use book_status({book_id}) to track progress.",
        }
    except Exception as e:
        session.rollback()
        return {"success": False, "error": str(e)}
    finally:
        session.close()


def _extract_book_worker(book_id: int, source_path: str, output_dir: str):
    """Background worker for book extraction.

    Routing:
      - PDF  → librarian.extract.extract_pdf (runs marker + grobid extractors,
               then the references domain builder)
      - EPUB → local ebooklib-based extractor (librarian.epub_extract)
    """
    import time

    source = Path(source_path)
    book_output = Path(output_dir)
    ext = source.suffix.lower()

    try:
        file_size_mb = source.stat().st_size / (1024 * 1024)

        if ext == ".pdf":
            from librarian.extract import extract_pdf

            _update_book_status(
                book_id, "extracting",
                f"Running PDF extractors on {source.name} ({file_size_mb:.1f} MB)...",
            )
            t0 = time.monotonic()
            extract_pdf(source, book_output)
            extraction_duration = time.monotonic() - t0

        elif ext == ".epub":
            from librarian.epub_extract import extract_epub

            _update_book_status(
                book_id, "extracting",
                f"Extracting {source.name} ({file_size_mb:.1f} MB) locally via ebooklib...",
            )
            t0 = time.monotonic()
            result = extract_epub(source, book_id, book_output)
            extraction_duration = time.monotonic() - t0

            if not result["success"]:
                _update_book_status(
                    book_id, "failed", result["error"] or "EPUB extraction failed",
                    extraction_duration_s=extraction_duration,
                )
                return

        else:
            _update_book_status(
                book_id, "failed",
                f"Unsupported extension {ext} (expected .pdf or .epub)",
            )
            return

        _update_book_status(
            book_id, "extracted",
            f"Extraction complete in {extraction_duration:.0f}s",
            converted_path=str(book_output),
            extraction_duration_s=extraction_duration,
        )
    except Exception as e:
        log.error(f"Extraction failed for book {book_id}: {e}")
        _update_book_status(book_id, "failed", str(e))


@mcp.tool()
def extract_book(book_id: int, force: bool = False) -> dict:
    """Extract a book to markdown.

    PDFs are extracted via the Spark marker service over HTTP; EPUBs are
    extracted locally via ebooklib. Launches in the background and returns
    immediately. Use book_status(book_id) to track progress.

    Args:
        book_id: ID of the book to extract
        force: Re-extract even if already extracted/indexed
    """
    config = _get_config()

    session = get_session(config)
    try:
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

        supported = {".pdf", ".epub"}
        if source.suffix.lower() not in supported:
            return {"success": False, "error": f"Unsupported format {source.suffix}, need PDF or EPUB"}

        from librarian.config import expand_path

        output_path = expand_path(config["output_path"])
        book_output = output_path / str(book_id)
        book_output.mkdir(parents=True, exist_ok=True)

        # Mark as extracting and launch background worker
        book.status = "extracting"
        book.status_message = "Starting extraction..."
        session.commit()

        thread = threading.Thread(
            target=_extract_book_worker,
            args=(book_id, book.source_path, str(book_output)),
            daemon=True,
        )
        thread.start()

        return {
            "success": True,
            "book_id": book_id,
            "status": "extracting",
            "message": f"Extraction started for '{book.title}'. Use book_status({book_id}) to track progress.",
        }
    except Exception as e:
        session.rollback()
        return {"success": False, "error": str(e)}
    finally:
        session.close()


@mcp.tool()
def ingest_book(
    title: str,
    authors: list[str] | None = None,
    format: str = "pdf",
    source_path: str | None = None,
) -> dict:
    """Register a new book in the library. Returns the assigned book ID.

    The source file must already exist on the data volume. Paths should start
    with /data/librarian/ (the container mount point). If a book with the same
    title already exists, returns the existing record instead of creating a
    duplicate. To upload new files, call upload_book() for the HTTP endpoint
    and curl example.

    Args:
        title: Book title
        authors: List of author names
        format: File format (pdf, epub, kindle)
        source_path: Absolute path to the source file on the data volume
    """
    config = _get_config()

    # Validate source_path exists on disk
    if source_path:
        if not Path(source_path).exists():
            return {
                "success": False,
                "error": f"Source file not found: {source_path}. "
                "Paths must be accessible inside the container (e.g. /data/librarian/...). "
                "Use POST /upload to upload files directly.",
            }

    session = get_session(config)
    try:
        # Check for duplicate by title (case-insensitive)
        existing = session.query(Book).filter(
            Book.title.ilike(title)
        ).first()
        if existing:
            return {
                "success": True,
                "book_id": existing.id,
                "title": existing.title,
                "status": existing.status,
                "already_exists": True,
            }

        # Check for duplicate by source_path
        if source_path:
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
        session.commit()

        return {
            "success": True,
            "book_id": book.id,
            "title": book.title,
            "status": book.status,
            "next_steps": [
                f"extract_book(book_id={book.id}) — extract to searchable text via cloud GPU",
                f"index_book(book_id={book.id}) — embed and store in vector search",
                f"suggest_tags(book_id={book.id}) — get subject/library tag suggestions",
            ],
        }
    except Exception as e:
        session.rollback()
        return {"success": False, "error": str(e)}
    finally:
        session.close()


@mcp.tool()
def list_books(status: str | None = None) -> list[dict]:
    """List all books in the library with metadata.

    Args:
        status: Filter by status (pending, extracted, indexed, failed)
    """
    config = _get_config()
    session = get_session(config)
    try:
        q = session.query(Book)
        if status:
            q = q.filter(Book.status == status)
        q = q.order_by(Book.id)

        return [
            {
                "id": b.id,
                "title": b.title,
                "authors": b.authors or [],
                "format": b.format,
                "status": b.status,
                "subjects": b.subjects or [],
                "library": b.library,
                "source_path": b.source_path,
            }
            for b in q.all()
        ]
    finally:
        session.close()


@mcp.tool()
def update_book(
    book_id: int,
    title: str | None = None,
    authors: list[str] | None = None,
    subjects: list[str] | None = None,
    library: str | None = None,
) -> dict:
    """Update book metadata. Use this to tag books with subjects and library.

    Subjects use slash-separated taxonomy (e.g. "therapy/dbt", "cs/networking").
    Library groups books into named collections (e.g. "therapy-core", "biology").

    Args:
        book_id: ID of the book to update
        title: New title (if correcting)
        authors: New author list (replaces existing)
        subjects: Subject tags (replaces existing)
        library: Library/collection name
    """
    config = _get_config()
    session = get_session(config)
    try:
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

        # Propagate searchable metadata to pgvector chunks
        vector_updates = {}
        if library is not None:
            vector_updates[META_LIBRARY] = library
        if subjects is not None:
            vector_updates[META_SUBJECTS] = serialize_list_metadata(subjects)

        chunks_updated = 0
        if vector_updates:
            try:
                from librarian.vectorstore import get_collection_names, get_vector_store

                store = get_vector_store(config)
                collections = get_collection_names(config)
                for coll in [collections["full"], collections["equations"], collections["chapters"]]:
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
    except Exception as e:
        session.rollback()
        return {"success": False, "error": str(e)}
    finally:
        session.close()


@mcp.tool()
def delete_book(book_id: int) -> dict:
    """Delete a book record and its indexed chunks.

    Removes the book from the database and deletes any associated vectors
    from pgvector. Does NOT delete source files or extracted content from disk.

    Args:
        book_id: ID of the book to delete
    """
    config = _get_config()
    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        title = book.title

        # Delete vectors from pgvector
        try:
            from librarian.vectorstore import get_collection_names, get_vector_store

            store = get_vector_store(config)
            collections = get_collection_names(config)
            for coll in [collections["full"], collections["equations"], collections["chapters"]]:
                store.delete_by_filter(coll, "book_id", book_id)
        except Exception:
            pass  # OK if vectors don't exist

        session.delete(book)
        session.commit()

        return {
            "success": True,
            "book_id": book_id,
            "title": title,
            "deleted": True,
        }
    except Exception as e:
        session.rollback()
        return {"success": False, "error": str(e)}
    finally:
        session.close()


@mcp.tool()
def book_status(book_id: int) -> dict:
    """Get the current status and metadata for a specific book.

    Args:
        book_id: ID of the book to check
    """
    config = _get_config()
    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        return {
            "success": True,
            "id": book.id,
            "title": book.title,
            "authors": book.authors or [],
            "status": book.status,
            "status_message": book.status_message,
            "format": book.format,
            "source_path": book.source_path,
            "converted_path": book.converted_path,
            "subjects": book.subjects or [],
            "library": book.library,
            "extraction_duration_s": book.extraction_duration_s,
            "created_at": str(book.created_at) if book.created_at else None,
            "updated_at": str(book.updated_at) if book.updated_at else None,
        }
    finally:
        session.close()


@mcp.tool()
def library_profile() -> dict:
    """Oriented summary of library state for agent onboarding.

    Returns what's available, what's well-covered, what's missing, and what
    filter values exist — so an agent can understand the library without
    trial-and-error discovery. Call this first when starting a new session.
    """
    from sqlalchemy import func

    config = _get_config()
    session = get_session(config)
    try:
        books = session.query(Book).order_by(Book.id).all()

        # Collect all subjects and libraries in use
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
                for s in b.subjects:
                    all_subjects.add(s)
            else:
                untagged.append({"id": b.id, "title": b.title})

        # Chunk counts per book from pgvector
        chunk_counts = {}
        try:
            from librarian.vectorstore import get_collection_names, get_vector_store

            store = get_vector_store(config)
            collections = get_collection_names(config)
            raw = store.get_metadata_counts(collections["full"], "book_id")
            chunk_counts = {int(k): v for k, v in raw.items()}
        except Exception:
            pass

        # Build collections summary
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
                "total_books": len(books),
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
    finally:
        session.close()


@mcp.tool()
def upload_book() -> dict:
    """Get upload instructions for adding a new book to the library.

    Call this to learn how to upload files. Books are uploaded via HTTP POST
    (multipart/form-data), then processed with extract_book and index_book.
    Do NOT pass file contents through MCP — use the HTTP endpoint directly.
    """
    config = _get_config()
    base = config.get("public_url")
    if not base:
        host = config.get("host", "localhost")
        port = config.get("port", 8811)
        base = f"http://{host}:{port}"
    endpoint = f"{base.rstrip('/')}/upload"

    return {
        "endpoint": endpoint,
        "method": "POST",
        "content_type": "multipart/form-data",
        "fields": {
            "file": "required — PDF or EPUB file",
            "title": "optional — defaults to filename",
            "authors": "optional — comma-separated names",
        },
        "example": f"curl -F file=@book.pdf -F title='My Book' {endpoint}",
        "note": (
            "Returns book_id on success (with dedup — re-uploading same title "
            "returns existing record). Then call extract_book(book_id) and "
            "index_book(book_id) to process."
        ),
    }


# ---------------------------------------------------------------------------
# HTTP upload endpoint (not MCP — for agents that can curl/POST files)
# ---------------------------------------------------------------------------


@mcp.custom_route("/upload", methods=["POST"])
async def handle_upload(request):
    """Upload a book file via multipart POST.

    curl -F file=@book.pdf -F title="Book Title" -F authors="A, B" \
         http://localhost:8811/upload

    Returns JSON with book_id and next pipeline steps.
    Deduplicates by title and source_path — re-uploading the same book
    returns the existing record instead of creating a duplicate.
    """
    from starlette.responses import JSONResponse

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        return JSONResponse(
            {"success": False, "error": "Content-Type must be multipart/form-data"},
            status_code=400,
        )

    form = await request.form()

    upload = form.get("file")
    if not upload:
        return JSONResponse(
            {"success": False, "error": "Missing 'file' field"},
            status_code=400,
        )

    filename = upload.filename
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".epub"}:
        return JSONResponse(
            {"success": False, "error": f"Unsupported format {suffix}, need .pdf or .epub"},
            status_code=400,
        )

    file_bytes = await upload.read()

    title = form.get("title") or Path(filename).stem
    authors_raw = form.get("authors", "")
    authors = [a.strip() for a in authors_raw.split(",") if a.strip()] if authors_raw else []

    # Write to intake directory
    from librarian.config import expand_path

    config = _get_config()
    intake_path = expand_path(config.get("intake_path", "~/data/librarian/intake/ebooks"))
    intake_path.mkdir(parents=True, exist_ok=True)
    dest = intake_path / filename
    dest.write_bytes(file_bytes)

    # Duplicate detection, then create book record
    session = get_session(config)
    try:
        # Check by title (case-insensitive)
        existing = session.query(Book).filter(Book.title.ilike(title)).first()
        if not existing:
            # Check by source_path
            existing = session.query(Book).filter(
                Book.source_path == str(dest)
            ).first()

        if existing:
            return JSONResponse({
                "success": True,
                "book_id": existing.id,
                "title": existing.title,
                "status": existing.status,
                "already_exists": True,
            })

        book = Book(
            title=title,
            authors=authors,
            format=suffix.lstrip("."),
            source_path=str(dest),
            status="pending",
        )
        session.add(book)
        session.commit()
        book_id = book.id
    except Exception as e:
        session.rollback()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        session.close()

    return JSONResponse({
        "success": True,
        "book_id": book_id,
        "title": title,
        "authors": authors,
        "source_path": str(dest),
        "size_bytes": len(file_bytes),
        "next_steps": [
            f"extract_book(book_id={book_id}) — extract to searchable text via cloud GPU",
            f"index_book(book_id={book_id}) — embed and store in vector search",
            f"suggest_tags(book_id={book_id}) — get subject/library tag suggestions",
            f"update_book(book_id={book_id}, subjects=..., library=...) — apply tags",
        ],
    })


# ---------------------------------------------------------------------------
# HTTP download endpoint
# ---------------------------------------------------------------------------


@mcp.custom_route("/download/{book_id}", methods=["GET"])
async def handle_download(request):
    """Download the original source file for a book.

    GET http://localhost:8811/download/42
    """
    from starlette.responses import FileResponse, JSONResponse

    book_id_str = request.path_params.get("book_id")
    try:
        book_id = int(book_id_str)
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "error": "Invalid book_id"},
            status_code=400,
        )

    config = _get_config()
    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return JSONResponse(
                {"success": False, "error": f"Book {book_id} not found"},
                status_code=404,
            )
        if not book.source_path:
            return JSONResponse(
                {"success": False, "error": f"Book {book_id} has no source file"},
                status_code=404,
            )
        source = Path(book.source_path)
        if not source.exists():
            return JSONResponse(
                {"success": False, "error": f"Source file not found on disk: {source.name}"},
                status_code=404,
            )
        title_slug = book.title[:60].replace(" ", "_").replace("/", "-")
        filename = f"{title_slug}{source.suffix}"
    finally:
        session.close()

    return FileResponse(
        path=str(source),
        filename=filename,
        media_type="application/octet-stream",
    )


@mcp.tool()
def download_book(book_id: int) -> dict:
    """Get a download link for the original source file of a book.

    Returns an HTTP URL that can be used to download the file directly.

    Args:
        book_id: ID of the book to download
    """
    config = _get_config()
    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}
        if not book.source_path:
            return {"success": False, "error": f"Book {book_id} has no source file"}
        source = Path(book.source_path)
        if not source.exists():
            return {"success": False, "error": f"Source file not found on disk: {source.name}"}

        base = config.get("public_url")
        if not base:
            host = config.get("host", "localhost")
            port = config.get("port", 8811)
            base = f"http://{host}:{port}"
        url = f"{base.rstrip('/')}/download/{book_id}"

        return {
            "success": True,
            "book_id": book_id,
            "title": book.title,
            "format": book.format,
            "download_url": url,
            "size_bytes": source.stat().st_size,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Book verification / QA
# ---------------------------------------------------------------------------


def _assess(status: str, details: dict, issues: list[str]) -> dict:
    """Build a verification dimension result."""
    return {"status": status, "details": details, "issues": issues}


def _check_garbled(text: str) -> list[str]:
    """Check a text sample for OCR quality issues."""
    import re

    issues = []
    words = text.split()
    if not words:
        return ["Empty text"]

    # Average word length — very short suggests garbled extraction
    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len < 2.5:
        issues.append(f"Very short average word length ({avg_len:.1f})")

    # Excessive special characters (non-alphanumeric, non-punctuation)
    alpha_count = sum(1 for c in text if c.isalnum() or c.isspace())
    alpha_ratio = alpha_count / len(text) if text else 0
    if alpha_ratio < 0.6:
        issues.append(f"Low alphanumeric ratio ({alpha_ratio:.0%})")

    # Repeated characters (e.g., "aaaa" or "????")
    repeated = re.findall(r'(.)\1{4,}', text)
    if repeated:
        issues.append(f"Repeated character runs: {''.join(set(repeated))}")

    # Encoding artifacts
    encoding_markers = ['â€™', 'â€"', 'â€œ', 'Ã©', 'Ã¡', 'ï¬', '\ufffd']
    found = [m for m in encoding_markers if m in text]
    if found:
        issues.append(f"Encoding artifacts: {', '.join(found[:3])}")

    return issues


@mcp.tool()
def verify_book(book_id: int) -> dict:
    """Thorough post-indexing QA for a book. Checks structure, completeness,
    OCR quality, landmarks, metadata, equations, and chapter detection.

    Each dimension returns green/yellow/red status with details and issues.
    Run this after indexing to verify quality before declaring a book done.

    Args:
        book_id: ID of the book to verify
    """
    import json
    import random
    import re

    from librarian.config import expand_path
    from librarian.index import load_extracted_blocks, load_extracted_book
    from librarian.structure import (
        extract_structure_from_blocks,
        parse_structure,
        validate_structure,
    )
    from librarian.vectorstore import get_collection_names, get_vector_store

    config = _get_config()
    session = get_session(config)
    results = {}

    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        # ── 1. Metadata ─────────────────────────────────────────────
        meta_issues = []
        if not book.title:
            meta_issues.append("Missing title")
        if not book.authors:
            meta_issues.append("Missing authors")
        if not book.subjects:
            meta_issues.append("Missing subjects (use update_book to tag)")
        if not book.library:
            meta_issues.append("Missing library (use update_book to assign)")

        meta_status = "green"
        if not book.title or not book.authors:
            meta_status = "red"
        elif not book.subjects or not book.library:
            meta_status = "yellow"

        results["metadata"] = _assess(meta_status, {
            "title": book.title,
            "authors": book.authors or [],
            "subjects": book.subjects or [],
            "library": book.library,
            "format": book.format,
            "status": book.status,
            "extraction_duration_s": book.extraction_duration_s,
        }, meta_issues)

        # ── 2. Source Files ───────────────────────────────────────
        from pathlib import Path

        src_issues = []
        src_details = {}

        if book.source_path:
            src = Path(book.source_path)
            src_details["source_path"] = book.source_path
            if src.exists():
                src_details["source_exists"] = True
                src_details["source_size_bytes"] = src.stat().st_size
            else:
                src_details["source_exists"] = False
                src_issues.append(f"Source file missing: {book.source_path}")
        else:
            src_details["source_exists"] = False
            src_issues.append("No source_path set")

        if book.converted_path:
            conv = Path(book.converted_path)
            src_details["converted_path"] = book.converted_path
            if conv.exists():
                src_details["converted_exists"] = True
            else:
                src_details["converted_exists"] = False
                src_issues.append(f"Converted file missing: {book.converted_path}")

        src_status = "green"
        if not book.source_path or (book.source_path and not Path(book.source_path).exists()):
            src_status = "red"
        elif src_issues:
            src_status = "yellow"

        results["source_files"] = _assess(src_status, src_details, src_issues)

        # ── 3. Load extracted content ───────────────────────────────
        output_path = expand_path(config["output_path"])
        book_dir = output_path / str(book_id)

        if not book_dir.exists():
            results["extraction"] = _assess("red", {}, ["No extracted content directory found"])
            return {"success": True, "book_id": book_id, "verification": results}

        blocks = load_extracted_blocks(book_dir)
        content, raw_content = load_extracted_book(book_dir)

        if not content:
            results["extraction"] = _assess("red", {}, ["No extracted markdown found"])
            return {"success": True, "book_id": book_id, "verification": results}

        # ── 4. Structure / Chapters ─────────────────────────────────
        if blocks:
            structure = extract_structure_from_blocks(blocks, title=book.title or "")
            pages = [b.get("page") for b in blocks if b.get("page")]
            total_pages = (max(pages) - min(pages) + 1) if pages else None
            structure_source = "blocks"
        else:
            structure = parse_structure(raw_content, title=book.title or "")
            total_pages = None
            structure_source = "markdown"

        validation = validate_structure(structure, total_pages)
        ch_count = validation["chapter_count"]

        struct_issues = list(validation.get("warnings", []))
        if structure_source == "markdown":
            struct_issues.append("Using markdown fallback (no JSON blocks) — page numbers may be unreliable")
        if ch_count == 0 and "No chapters detected" not in struct_issues:
            struct_issues.append("No chapters detected — headers may not match known patterns (Chapter N, Rule No. N, Part N, Cycle N, N. Title)")

        # Check chapter title quality
        chapters_without_title = [ch for ch in structure.chapters if not ch.title]
        if chapters_without_title:
            struct_issues.append(
                f"{len(chapters_without_title)} chapters missing titles: "
                + ", ".join(f"Ch {ch.number}" for ch in chapters_without_title[:5])
            )

        struct_status = "green"
        if ch_count == 0:
            struct_status = "red"
        elif struct_issues:
            struct_status = "yellow"

        chapter_details = []
        for ch in structure.chapters:
            chapter_details.append({
                "number": ch.number,
                "title": ch.title or "(untitled)",
                "page_start": ch.page_start,
                "page_end": ch.page_end,
                "sections": len(ch.sections),
            })

        results["structure"] = _assess(struct_status, {
            "source": structure_source,
            "chapter_count": ch_count,
            "chapters_with_pages": validation["chapters_with_pages"],
            "page_coverage": round(validation["page_coverage"], 2),
            "total_pages": total_pages,
            "chapters": chapter_details,
        }, struct_issues)

        # ── 5. Chunk Analysis from pgvector ─────────────────────────
        store = get_vector_store(config)
        collections = get_collection_names(config)

        # Query chunk stats via raw SQL
        conn = store._get_psycopg_conn()
        table = f"data_{collections['full']}"

        # Total chunks
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE metadata_->>'book_id' = %s",
            (str(book_id),),
        )
        chunk_count = cur.fetchone()[0]

        if chunk_count == 0:
            results["completeness"] = _assess("red", {"chunks": 0}, ["No chunks in vector store"])
            return {"success": True, "book_id": book_id, "verification": results}

        # Chapter coverage in chunks
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE metadata_->>'book_id' = %s "
            f"  AND metadata_->>'chapter_num' IS NOT NULL "
            f"  AND metadata_->>'chapter_num' != 'null' "
            f"  AND metadata_->>'chapter_num' != ''",
            (str(book_id),),
        )
        chunks_with_chapter = cur.fetchone()[0]

        # Page distribution
        cur = conn.execute(
            f"SELECT "
            f"  MIN((metadata_->>'page')::int) FILTER (WHERE metadata_->>'page' IS NOT NULL AND metadata_->>'page' != 'null'), "
            f"  MAX((metadata_->>'page')::int) FILTER (WHERE metadata_->>'page' IS NOT NULL AND metadata_->>'page' != 'null'), "
            f"  COUNT(DISTINCT (metadata_->>'page')::int) FILTER (WHERE metadata_->>'page' IS NOT NULL AND metadata_->>'page' != 'null'), "
            f"  COUNT(*) FILTER (WHERE metadata_->>'page' IS NULL OR metadata_->>'page' = 'null') "
            f"FROM {table} WHERE metadata_->>'book_id' = %s",
            (str(book_id),),
        )
        page_row = cur.fetchone()
        min_page, max_page, distinct_pages, chunks_no_page = page_row

        # Block type distribution
        cur = conn.execute(
            f"SELECT COALESCE(metadata_->>'block_type', 'Unknown'), COUNT(*) "
            f"FROM {table} WHERE metadata_->>'book_id' = %s "
            f"GROUP BY 1 ORDER BY 2 DESC",
            (str(book_id),),
        )
        block_type_dist = {row[0]: row[1] for row in cur.fetchall()}

        # Distinct chapter numbers in chunks
        cur = conn.execute(
            f"SELECT DISTINCT (metadata_->>'chapter_num')::int "
            f"FROM {table} "
            f"WHERE metadata_->>'book_id' = %s "
            f"  AND metadata_->>'chapter_num' IS NOT NULL "
            f"  AND metadata_->>'chapter_num' != 'null' "
            f"  AND metadata_->>'chapter_num' != '' "
            f"ORDER BY 1",
            (str(book_id),),
        )
        indexed_chapter_nums = [row[0] for row in cur.fetchall()]

        chapter_coverage = chunks_with_chapter / chunk_count if chunk_count else 0

        completeness_issues = []
        if chunks_no_page and chunks_no_page > chunk_count * 0.3:
            completeness_issues.append(f"{chunks_no_page}/{chunk_count} chunks missing page numbers")
        if chapter_coverage < 0.5:
            completeness_issues.append(
                f"Only {chapter_coverage:.0%} of chunks have chapter metadata "
                f"({chunks_with_chapter}/{chunk_count})"
            )
        page_span = (max_page - min_page + 1) if (min_page is not None and max_page is not None) else None
        if page_span and distinct_pages and distinct_pages < page_span * 0.5:
            completeness_issues.append(
                f"Only {distinct_pages}/{page_span} distinct pages represented in chunks"
            )

        # Check for page gaps within the actual page range
        if min_page is not None and max_page is not None:
            expected_pages = set(range(min_page, max_page + 1))
            # Sample check for large gaps (query a sample of page numbers)
            cur = conn.execute(
                f"SELECT DISTINCT (metadata_->>'page')::int "
                f"FROM {table} WHERE metadata_->>'book_id' = %s "
                f"  AND metadata_->>'page' IS NOT NULL AND metadata_->>'page' != 'null' "
                f"ORDER BY 1",
                (str(book_id),),
            )
            actual_pages = {row[0] for row in cur.fetchall()}
            missing = expected_pages - actual_pages
            if len(missing) > 5:
                # Find contiguous gaps
                missing_sorted = sorted(missing)
                gaps = []
                gap_start = missing_sorted[0]
                gap_end = missing_sorted[0]
                for p in missing_sorted[1:]:
                    if p == gap_end + 1:
                        gap_end = p
                    else:
                        gaps.append((gap_start, gap_end))
                        gap_start = p
                        gap_end = p
                gaps.append((gap_start, gap_end))
                gap_strs = [f"{s}-{e}" if s != e else str(s) for s, e in gaps[:5]]
                completeness_issues.append(
                    f"{len(missing)} pages missing from chunks. Gaps: {', '.join(gap_strs)}"
                    + (" ..." if len(gaps) > 5 else "")
                )

        comp_status = "green"
        if chunk_count < 10 or chapter_coverage < 0.3:
            comp_status = "red"
        elif completeness_issues:
            comp_status = "yellow"

        results["completeness"] = _assess(comp_status, {
            "total_chunks": chunk_count,
            "chunks_with_chapter": chunks_with_chapter,
            "chapter_coverage": f"{chapter_coverage:.0%}",
            "indexed_chapters": indexed_chapter_nums,
            "page_range": f"{min_page}-{max_page}" if min_page is not None else "unknown",
            "distinct_pages": distinct_pages,
            "total_pages": total_pages,
            "chunks_missing_page": chunks_no_page,
            "block_types": block_type_dist,
        }, completeness_issues)

        # ── 6. OCR Quality (sample chunks) ──────────────────────────
        cur = conn.execute(
            f"SELECT text FROM {table} WHERE metadata_->>'book_id' = %s "
            f"ORDER BY RANDOM() LIMIT 20",
            (str(book_id),),
        )
        sample_texts = [row[0] for row in cur.fetchall() if row[0]]

        ocr_issues = []
        garbled_count = 0
        checked_count = 0
        for text in sample_texts:
            # Skip very short chunks (verse numbers, page markers, etc.)
            if len(text.strip()) < 30:
                continue
            checked_count += 1
            chunk_issues = _check_garbled(text)
            if chunk_issues:
                garbled_count += 1
                if len(ocr_issues) < 3:  # Only report first 3
                    preview = text[:80].replace('\n', ' ')
                    ocr_issues.append(f"Sample: '{preview}...' — {'; '.join(chunk_issues)}")

        garbled_ratio = garbled_count / checked_count if checked_count else 0
        ocr_status = "green"
        if garbled_ratio > 0.3:
            ocr_status = "red"
        elif garbled_ratio > 0.1:
            ocr_status = "yellow"

        results["ocr_quality"] = _assess(ocr_status, {
            "samples_checked": checked_count,
            "samples_with_issues": garbled_count,
            "issue_ratio": f"{garbled_ratio:.0%}",
        }, ocr_issues)

        # ── 7. Landmarks ────────────────────────────────────────────
        landmark_keywords = {
            "table_of_contents": ["table of contents", "contents"],
            "bibliography": ["bibliography", "references cited", "works cited"],
            "index": ["\\bindex\\b"],  # word boundary to avoid matching "indexing" etc.
            "appendix": ["appendix"],
            "glossary": ["glossary"],
            "preface": ["preface", "foreword"],
            "introduction": ["introduction"],
        }

        landmarks_found = {}
        for landmark, keywords in landmark_keywords.items():
            # Check in section headers first
            cur = conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE metadata_->>'book_id' = %s "
                f"  AND metadata_->>'block_type' = 'SectionHeader' "
                f"  AND text ~* %s",
                (str(book_id), "|".join(keywords)),
            )
            header_count = cur.fetchone()[0]
            if header_count > 0:
                landmarks_found[landmark] = "found_in_headers"
            else:
                # Check in any chunk
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE metadata_->>'book_id' = %s AND text ~* %s LIMIT 1",
                    (str(book_id), "|".join(keywords)),
                )
                if cur.fetchone()[0] > 0:
                    landmarks_found[landmark] = "found_in_text"

        landmark_issues = []
        # Most books should have at least an introduction
        if "introduction" not in landmarks_found and "preface" not in landmarks_found:
            landmark_issues.append("No introduction or preface found")
        # Having a TOC is good
        if "table_of_contents" not in landmarks_found:
            landmark_issues.append("No table of contents found (may be normal for some books)")

        landmark_status = "green"
        if not landmarks_found:
            landmark_status = "yellow"

        results["landmarks"] = _assess(landmark_status, {
            "found": landmarks_found,
        }, landmark_issues)

        # ── 8. Equations ────────────────────────────────────────────
        eq_table = f"data_{collections['equations']}"
        try:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM {eq_table} WHERE metadata_->>'book_id' = %s",
                (str(book_id),),
            )
            eq_count = cur.fetchone()[0]
        except Exception:
            eq_count = 0

        # Check if content has LaTeX
        latex_in_content = bool(re.search(r'\$\$.+?\$\$', raw_content, re.DOTALL)) if raw_content else False

        eq_issues = []
        if latex_in_content and eq_count == 0:
            eq_issues.append("LaTeX equations found in content but none indexed")
        eq_status = "green"
        if eq_issues:
            eq_status = "yellow"

        results["equations"] = _assess(eq_status, {
            "indexed_equations": eq_count,
            "latex_in_content": latex_in_content,
        }, eq_issues)

        # ── 9. Chapter Summaries ────────────────────────────────────
        ch_table = f"data_{collections['chapters']}"
        try:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM {ch_table} WHERE metadata_->>'book_id' = %s",
                (str(book_id),),
            )
            ch_indexed = cur.fetchone()[0]
        except Exception:
            ch_indexed = 0

        ch_issues = []
        if ch_count > 0 and ch_indexed == 0:
            ch_issues.append(f"Structure has {ch_count} chapters but none are indexed with summaries")
        elif ch_count > 0 and ch_indexed < ch_count:
            ch_issues.append(f"Only {ch_indexed}/{ch_count} chapters have summaries")

        ch_status = "green"
        if ch_count > 0 and ch_indexed == 0:
            ch_status = "red"
        elif ch_issues:
            ch_status = "yellow"

        results["chapter_summaries"] = _assess(ch_status, {
            "detected_chapters": ch_count,
            "indexed_summaries": ch_indexed,
        }, ch_issues)

        # ── Overall ─────────────────────────────────────────────────
        statuses = [r["status"] for r in results.values()]
        if "red" in statuses:
            overall = "red"
        elif "yellow" in statuses:
            overall = "yellow"
        else:
            overall = "green"

        # Collect all issues across dimensions
        all_issues = []
        for dim, result in results.items():
            for issue in result["issues"]:
                all_issues.append(f"[{dim}] {issue}")

        return {
            "success": True,
            "book_id": book_id,
            "title": book.title,
            "overall_status": overall,
            "verification": results,
            "all_issues": all_issues,
        }

    except Exception as e:
        log.error(f"Verification failed for book {book_id}: {e}")
        return {"success": False, "error": str(e)}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Tag suggestion keywords → subjects
# ---------------------------------------------------------------------------

_TAG_RULES = [
    # therapy
    (["dialectical behavior", "dbt", "linehan", "distress tolerance", "emotion regulation"],
     "therapy/dbt"),
    (["cognitive behavioral", "cognitive behaviour", "cbt", "automatic thoughts", "thought record"],
     "therapy/cbt"),
    (["acceptance and commitment", "act ", "psychological flexibility", "defusion", "russ harris"],
     "therapy/act"),
    (["internal family systems", "ifs", "parts work", "self-energy", "richard schwartz"],
     "therapy/ifs"),
    (["existential", "logotherapy", "meaning-centered", "irvin yalom"],
     "therapy/existential"),
    (["psychotherapy", "therapeutic", "clinician", "therapist", "mental health"],
     "therapy"),
    # biology / science
    (["biology", "evolution", "ecology", "species", "organism"],
     "biology"),
    (["neuroscience", "brain", "neural", "cortex", "synapse"],
     "biology/neuroscience"),
    (["bioacoustics", "animal communication", "vocalization", "call structure"],
     "biology/bioacoustics"),
    # cs / tech
    (["algorithm", "data structure", "computer science", "programming"],
     "cs"),
    (["networking", "tcp", "protocol", "routing", "packet"],
     "cs/networking"),
    (["machine learning", "deep learning", "neural network", "training data"],
     "cs/ml"),
    (["cryptography", "bitcoin", "blockchain", "distributed ledger"],
     "cs/crypto"),
]

_LIBRARY_RULES = [
    (["therapy", "therapeutic", "psychotherapy", "clinician", "mental health",
      "dbt", "cbt", "act ", "ifs", "counseling"], "therapy-core"),
    (["biology", "ecology", "evolution", "species", "organism", "bioacoustics"],
     "biology"),
    (["computer", "programming", "algorithm", "software", "networking"],
     "cs"),
]


@mcp.tool()
def suggest_tags(book_id: int) -> dict:
    """Suggest subjects and library for a book based on its content.

    Reads the title, authors, and first ~2000 tokens of extracted text, then
    matches against the existing taxonomy using keyword heuristics. Returns
    suggestions that can be applied via update_book.

    Args:
        book_id: ID of the book to analyze
    """
    from librarian.config import expand_path

    config = _get_config()
    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        # Build text sample from title + authors + extracted content
        parts = [book.title or ""]
        if book.authors:
            parts.extend(book.authors)

        # Read first ~2000 tokens from extracted markdown
        from librarian.files import marker_markdown

        output_path = expand_path(config["output_path"])
        md_file = marker_markdown(output_path / str(book_id))
        if md_file and md_file.exists():
            text = md_file.read_text(errors="replace")[:8000]  # ~2000 tokens
            parts.append(text)

        sample = " ".join(parts).lower()

        # Match subjects
        matched_subjects = []
        for keywords, subject in _TAG_RULES:
            if any(kw in sample for kw in keywords):
                matched_subjects.append(subject)

        # Deduplicate: if we matched therapy/dbt, drop bare "therapy"
        specific = [s for s in matched_subjects if "/" in s]
        if specific:
            matched_subjects = [s for s in matched_subjects if "/" in s or
                                not any(sp.startswith(s + "/") for sp in specific)]

        # Match library
        suggested_library = None
        for keywords, library in _LIBRARY_RULES:
            if any(kw in sample for kw in keywords):
                suggested_library = library
                break

        # Get existing taxonomy for context
        all_subjects = set()
        for b in session.query(Book).all():
            if b.subjects:
                all_subjects.update(b.subjects)

        return {
            "success": True,
            "book_id": book_id,
            "title": book.title,
            "current_subjects": book.subjects or [],
            "current_library": book.library,
            "suggested_subjects": matched_subjects,
            "suggested_library": suggested_library,
            "existing_taxonomy": sorted(all_subjects),
            "hint": "Review suggestions and apply with update_book(book_id, subjects=..., library=...)",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over streamable HTTP."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
