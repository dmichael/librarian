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
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from librarian.config import load_config
from librarian.db import Book, get_session

log = logging.getLogger(__name__)

mcp = FastMCP("librarian", host="0.0.0.0", port=8811)

# ---------------------------------------------------------------------------
# Lazy singletons — heavy objects created once on first use
# ---------------------------------------------------------------------------

_config = None
_embed_model = None


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_embed_model():
    """Load embedding model once (takes a few seconds on first call)."""
    global _embed_model
    if _embed_model is None:
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
        nodes = [n for n in nodes if n.metadata.get("book_id") == book_id]

    results = []
    for node in nodes:
        meta = node.metadata
        results.append({
            "text": node.text,
            "score": round(node.score, 4),
            "title": meta.get("title", "Unknown"),
            "authors": meta.get("authors", ""),
            "book_id": meta.get("book_id"),
            "page": meta.get("page"),
            "chapter_num": meta.get("chapter_num"),
            "chapter_title": meta.get("chapter_title", ""),
            "block_type": meta.get("block_type", ""),
        })

    return results


@mcp.tool()
def index_book(book_id: int) -> dict:
    """Index an extracted book — embed chunks and store in pgvector.

    The book must already be extracted (converted/ directory must exist with
    JSON blocks and/or markdown). Updates book status to 'indexed'.

    Args:
        book_id: ID of the book to index
    """
    from llama_index.core import Settings

    from librarian.config import expand_path
    from librarian.index import (
        index_book as _index_book,
        load_extracted_blocks,
        load_extracted_book,
        setup_embedding_model,
    )
    from librarian.vectorstore import get_collection_names, get_vector_store

    config = _get_config()
    embed_model = _get_embed_model()
    Settings.embed_model = embed_model

    output_path = expand_path(config["output_path"])
    book_dir = output_path / str(book_id)

    if not book_dir.exists():
        return {"success": False, "error": f"No extracted content at {book_dir}"}

    # Get book metadata from books table
    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found in database"}

        metadata = {
            "id": book_id,
            "title": book.title,
            "authors": book.authors or [],
            "subjects": book.subjects or [],
            "tags": [],
            "*library": book.library or "",
            "source_path": book.source_path or "",
        }

        # Load content
        content, raw_content = load_extracted_book(book_dir)
        if not content:
            return {"success": False, "error": "No extracted markdown found"}

        blocks = load_extracted_blocks(book_dir)

        # Get vector stores
        store = get_vector_store(config)
        collections = get_collection_names(config)
        vector_store = store.get_llama_store(collections["full"])
        equation_store = store.get_llama_store(collections["equations"])
        chapter_store = store.get_llama_store(collections["chapters"])

        # Delete old entries first (idempotent re-index)
        for coll in [collections["full"], collections["equations"], collections["chapters"]]:
            store.delete_by_filter(coll, "book_id", book_id)

        chunks, eq_count, ch_count = _index_book(
            book_id, content, raw_content, metadata,
            vector_store, equation_store, chapter_store, config,
            blocks=blocks,
        )

        # Update book status
        book.status = "indexed"
        session.commit()

        return {
            "success": True,
            "book_id": book_id,
            "chunks": chunks,
            "equations": eq_count,
            "chapters": ch_count,
        }
    except Exception as e:
        session.rollback()
        return {"success": False, "error": str(e)}
    finally:
        session.close()


@mcp.tool()
def extract_book(book_id: int) -> dict:
    """Extract a book (PDF or EPUB) to markdown using Modal cloud GPUs.

    Reads the source file from the data volume, runs marker extraction on a
    cloud A100, and writes results to converted/{book_id}/. Updates book
    status to 'extracted'. Supports PDF and EPUB formats.

    Args:
        book_id: ID of the book to extract
    """
    config = _get_config()

    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

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

        # All formats go through Modal/marker for consistent quality
        try:
            import modal  # noqa: F401
        except ImportError:
            return {"success": False, "error": "Modal not installed. Add modal to dependencies."}

        from librarian.cloud_extract import app, extract_pdf_remote

        file_bytes = source.read_bytes()

        with app.run():
            result = extract_pdf_remote.remote(file_bytes, book_id, source.name)

        if not result["success"]:
            book.status = "failed"
            session.commit()
            return {"success": False, "error": result["error"]}

        # Write output files
        (book_output / f"{book_id}.json").write_text(result["chunks_json"])
        if result["meta_json"]:
            (book_output / f"{book_id}_meta.json").write_text(result["meta_json"])
        (book_output / f"{book_id}.md").write_text(result["markdown"])

        book.status = "extracted"
        book.converted_path = str(book_output)
        session.commit()

        return {
            "success": True,
            "book_id": book_id,
            "output_dir": str(book_output),
            "method": "modal_cloud",
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
    duplicate. For uploading files, prefer POST /upload instead.

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

        return {
            "success": True,
            "book_id": book.id,
            "title": book.title,
            "authors": book.authors or [],
            "subjects": book.subjects or [],
            "library": book.library,
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
def book_status() -> dict:
    """Pipeline statistics: book counts by status and total chunks indexed."""
    from sqlalchemy import func, text

    config = _get_config()
    session = get_session(config)
    try:
        # Counts by status
        rows = session.query(Book.status, func.count()).group_by(Book.status).all()
        by_status = {status: count for status, count in rows}
        total_books = sum(by_status.values())

        # Chunk count from pgvector
        try:
            from librarian.vectorstore import get_collection_names, get_vector_store

            store = get_vector_store(config)
            collections = get_collection_names(config)
            chunk_count = store.get_collection_count(collections["full"])
            eq_count = store.get_collection_count(collections["equations"])
        except Exception:
            chunk_count = -1
            eq_count = -1

        return {
            "total_books": total_books,
            "by_status": by_status,
            "total_chunks": chunk_count,
            "total_equations": eq_count,
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
                "Use upload_book to add new books to the library."
            ),
        }
    finally:
        session.close()


@mcp.tool()
def upload_book(
    filename: str,
    content_base64: str,
    title: str | None = None,
    authors: list[str] | None = None,
) -> dict:
    """Upload a book file, register it, and prepare it for extraction.

    Accepts a base64-encoded file (PDF or EPUB), writes it to the data volume,
    and creates a book record. Returns the book ID for use with extract_book.

    Args:
        filename: Original filename with extension (e.g. "book.pdf")
        content_base64: Base64-encoded file content
        title: Book title (defaults to filename stem if not provided)
        authors: List of author names
    """
    import base64

    config = _get_config()
    from librarian.config import expand_path

    # Validate format
    suffix = Path(filename).suffix.lower()
    supported = {".pdf", ".epub"}
    if suffix not in supported:
        return {"success": False, "error": f"Unsupported format {suffix}, need PDF or EPUB"}

    # Decode content
    try:
        file_bytes = base64.b64decode(content_base64)
    except Exception as e:
        return {"success": False, "error": f"Invalid base64 content: {e}"}

    # Write to intake directory
    intake_path = expand_path(config.get("intake_path", "~/data/librarian/intake/ebooks"))
    intake_path.mkdir(parents=True, exist_ok=True)
    dest = intake_path / filename
    dest.write_bytes(file_bytes)

    # Create book record (with duplicate check)
    book_title = title or Path(filename).stem
    fmt = suffix.lstrip(".")

    session = get_session(config)
    try:
        existing = session.query(Book).filter(Book.title.ilike(book_title)).first()
        if existing:
            return JSONResponse({
                "success": True,
                "book_id": existing.id,
                "title": existing.title,
                "status": existing.status,
                "already_exists": True,
            })

        book = Book(
            title=book_title,
            authors=authors,
            format=fmt,
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


# ---------------------------------------------------------------------------
# HTTP upload endpoint (not MCP — for agents that can curl/POST files)
# ---------------------------------------------------------------------------


@mcp.custom_route("/upload", methods=["POST"])
async def handle_upload(request):
    """Upload a book file via multipart POST.

    curl -F file=@book.pdf -F title="Book Title" -F authors="A, B" \
         http://agents.local:8811/upload

    Returns JSON with book_id and next pipeline steps.
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

    # Create book record
    session = get_session(config)
    try:
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
        output_path = expand_path(config["output_path"])
        md_file = output_path / str(book_id) / f"{book_id}.md"
        if md_file.exists():
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
