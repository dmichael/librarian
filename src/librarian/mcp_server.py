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

        if source.suffix.lower() == ".epub":
            # Native EPUB extraction — no GPU needed
            from librarian.epub_extract import extract_epub

            result = extract_epub(source, book_id, book_output)

            if not result["success"]:
                book.status = "failed"
                session.commit()
                return {"success": False, "error": result["error"]}

            book.status = "extracted"
            book.converted_path = str(book_output)
            session.commit()

            return {
                "success": True,
                "book_id": book_id,
                "output_dir": str(book_output),
                "method": "epub_native",
                "blocks": result["block_count"],
                "chapters": result["chapter_count"],
            }
        else:
            # PDF extraction via Modal cloud GPU
            try:
                import modal  # noqa: F401
            except ImportError:
                return {"success": False, "error": "Modal not installed. Add modal to dependencies."}

            from librarian.cloud_extract import app, extract_pdf_remote

            pdf_bytes = source.read_bytes()

            with app.run():
                result = extract_pdf_remote.remote(pdf_bytes, book_id, source.name)

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

    The source file should already exist on the data volume (e.g. under
    calibre/ or intake/). This just creates the database record.

    Args:
        title: Book title
        authors: List of author names
        format: File format (pdf, epub, kindle)
        source_path: Absolute path to the source file on the data volume
    """
    config = _get_config()
    session = get_session(config)
    try:
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

    # Create book record
    book_title = title or Path(filename).stem
    fmt = suffix.lstrip(".")

    session = get_session(config)
    try:
        book = Book(
            title=book_title,
            authors=authors or [],
            format=fmt,
            source_path=str(dest),
            status="pending",
        )
        session.add(book)
        session.commit()

        return {
            "success": True,
            "book_id": book.id,
            "title": book.title,
            "source_path": str(dest),
            "size_bytes": len(file_bytes),
        }
    except Exception as e:
        session.rollback()
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
