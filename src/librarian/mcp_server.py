"""Librarian MCP server.

Thin serving layer: every tool and HTTP route delegates to the library
modules (catalog, pipeline, verify, query, classify). Business logic does
not live here.

Run:
    python -m librarian.mcp_server          # default: 0.0.0.0:8811
    librarian-serve                         # via entry point
"""

import logging
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from librarian import catalog, images, pipeline
from librarian.config import load_config
from librarian.metadata_types import (
    build_search_result_row,
    build_text_search_result_row,
)

log = logging.getLogger(__name__)

mcp = FastMCP("librarian", host="0.0.0.0", port=8811)


_config = None


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_embed_model():
    """Load the shared embedding model (cached process-wide in librarian.embeddings)."""
    from llama_index.core import Settings

    from librarian.embeddings import get_embed_model

    model = get_embed_model(_get_config())
    Settings.embed_model = model
    return model


# ---------------------------------------------------------------------------
# Search tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search(
    query: str,
    top_k: int = 5,
    book_id: int | None = None,
    library: str | None = None,
    subjects: list[str] | None = None,
    block_type: str | None = None,
) -> list[dict]:
    """Search the library. Returns ranked passages with title, page, chapter, and score.

    Args:
        query: Natural language search query
        top_k: Number of results to return (default 5)
        book_id: Restrict search to a specific book ID
        library: Restrict search to a named library
        subjects: Filter by subject tags (e.g. ["psychology/*"])
        block_type: Restrict to a marker block type (e.g. "Code", "Table", "ListGroup")
    """
    from librarian.query import retrieve

    # Ensure embedding model is loaded
    _get_embed_model()

    config = _get_config()

    nodes = retrieve(
        query,
        config=config,
        top_k=top_k,
        subjects=subjects,
        library=library,
        block_type=block_type,
        book_id=book_id,
    )

    image_catalog_cache: dict[int, list[dict]] = {}
    return [
        build_search_result_row(
            text=node.text,
            score=node.score,
            metadata=node.metadata,
            images=images.images_for_metadata(
                config, node.metadata, catalog_cache=image_catalog_cache
            ),
        )
        for node in nodes
    ]


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

    return [build_text_search_result_row(text=text, metadata=meta) for text, meta in rows]


# ---------------------------------------------------------------------------
# Pipeline tools
# ---------------------------------------------------------------------------


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
    try:
        return pipeline.start_extraction(_get_config(), book_id, force=force)
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def index_book(book_id: int) -> dict:
    """Index an extracted book into the vector store.

    Launches indexing in the background and returns immediately.
    Use book_status(book_id) to track progress.

    Args:
        book_id: ID of the book to index
    """
    try:
        return pipeline.start_indexing(_get_config(), book_id)
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Catalog tools
# ---------------------------------------------------------------------------


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
    if source_path and not Path(source_path).exists():
        return {
            "success": False,
            "error": f"Source file not found: {source_path}. "
            "Paths must be accessible inside the container (e.g. /data/librarian/...). "
            "Use POST /upload to upload files directly.",
        }

    try:
        return catalog.register_book(
            _get_config(),
            title=title,
            authors=authors,
            format=format,
            source_path=source_path,
        )
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def list_books(status: str | None = None) -> list[dict]:
    """List all books in the library with metadata.

    Args:
        status: Filter by status (pending, extracted, indexed, failed)
    """
    return catalog.list_books(_get_config(), status=status)


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
    try:
        return catalog.update_book(
            _get_config(), book_id,
            title=title, authors=authors, subjects=subjects, library=library,
        )
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def delete_book(book_id: int) -> dict:
    """Delete a book record and its indexed chunks.

    Removes the book from the database and deletes any associated vectors
    from the vector store. Does NOT delete source files or extracted content
    from disk.

    Args:
        book_id: ID of the book to delete
    """
    try:
        return catalog.delete_book(_get_config(), book_id)
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def book_status(book_id: int) -> dict:
    """Get the current status and metadata for a specific book.

    Args:
        book_id: ID of the book to check
    """
    return catalog.get_book_status(_get_config(), book_id)


@mcp.tool()
def library_profile() -> dict:
    """Oriented summary of library state for agent onboarding.

    Returns what's available, what's well-covered, what's missing, and what
    filter values exist — so an agent can understand the library without
    trial-and-error discovery. Call this first when starting a new session.
    """
    return catalog.library_profile(_get_config())


@mcp.tool()
def upload_book() -> dict:
    """Get upload instructions for adding a new book to the library.

    Call this to learn how to upload files. Books are uploaded via HTTP POST
    (multipart/form-data), then processed with extract_book and index_book.
    Do NOT pass file contents through MCP — use the HTTP endpoint directly.
    """
    endpoint = f"{catalog.public_base_url(_get_config())}/upload"

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


@mcp.tool()
def download_book(book_id: int) -> dict:
    """Get a download link for the original source file of a book.

    Returns an HTTP URL that can be used to download the file directly.

    Args:
        book_id: ID of the book to download
    """
    return catalog.download_info(_get_config(), book_id)


@mcp.tool()
def list_book_images(book_id: int) -> dict:
    """List images extracted from a book.

    Returns metadata and HTTP URLs for Marker-extracted JPEG/PNG/etc. images.
    These include figures, diagrams, pictures, and similar visual document
    regions. Use get_book_image for one image's metadata or fetch the returned
    URL to download the binary image.

    Args:
        book_id: ID of the book to inspect
    """
    return images.list_book_images(_get_config(), book_id)


@mcp.tool()
def get_book_image(book_id: int, image_id: str) -> dict:
    """Get metadata and a download URL for one extracted book image.

    Args:
        book_id: ID of the book
        image_id: Stable image ID returned by list_book_images
    """
    return images.get_book_image(_get_config(), book_id, image_id)


# ---------------------------------------------------------------------------
# QA / tagging tools
# ---------------------------------------------------------------------------


@mcp.tool()
def verify_book(book_id: int) -> dict:
    """Thorough post-indexing QA for a book. Checks structure, completeness,
    OCR quality, landmarks, metadata, equations, and chapter detection.

    Each dimension returns green/yellow/red status with details and issues.
    Run this after indexing to verify quality before declaring a book done.

    Args:
        book_id: ID of the book to verify
    """
    from librarian.verify import verify_book as _verify_book

    try:
        return _verify_book(_get_config(), book_id)
    except Exception as e:
        log.error(f"Verification failed for book {book_id}: {e}")
        return {"success": False, "error": str(e)}


@mcp.tool()
def suggest_tags(book_id: int) -> dict:
    """Suggest subjects and library for a book using the configured LLM.

    Reads the title, authors, and a content sample, plus the library's existing
    taxonomy, and asks the LLM for slash-format subjects and a library. The
    existing taxonomy guides naming/dedup but is not a closed list — the LLM
    proposes well-formed new tags when a book introduces new topics, so tagging
    grows the library rather than only matching what's already there. Falls back
    to keyword heuristics if the LLM is unreachable. Apply with update_book.

    Args:
        book_id: ID of the book to analyze
    """
    from librarian.classify import suggest_tags_for_book

    try:
        return suggest_tags_for_book(_get_config(), book_id)
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# HTTP routes (not MCP — for agents that can curl/POST files)
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

    try:
        result = catalog.register_book(
            config,
            title=title,
            authors=authors,
            format=suffix.lstrip("."),
            source_path=str(dest),
        )
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    if not result.get("already_exists"):
        result.update({
            "authors": authors,
            "source_path": str(dest),
            "size_bytes": len(file_bytes),
        })
    return JSONResponse(result)


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

    source, filename, error = catalog.source_file_for_download(_get_config(), book_id)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=404)

    return FileResponse(
        path=str(source),
        filename=filename,
        media_type="application/octet-stream",
    )


@mcp.custom_route("/books/{book_id}/images", methods=["GET"])
async def handle_list_book_images(request):
    """List extracted images for a book.

    GET http://localhost:8811/books/42/images
    """
    from starlette.responses import JSONResponse

    book_id_str = request.path_params.get("book_id")
    try:
        book_id = int(book_id_str)
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "error": "Invalid book_id"},
            status_code=400,
        )

    return JSONResponse(images.list_book_images(_get_config(), book_id))


@mcp.custom_route("/books/{book_id}/images/{image_id}", methods=["GET"])
async def handle_get_book_image(request):
    """Download one extracted image for a book.

    GET http://localhost:8811/books/42/images/marker%3A_page_27_Figure_2.jpeg
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

    image_id = request.path_params.get("image_id") or ""
    path, error = images.resolve_image_path(_get_config(), book_id, image_id)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=404)

    metadata = images.get_book_image(_get_config(), book_id, image_id)
    image = metadata.get("image", {})
    return FileResponse(
        path=str(path),
        filename=image.get("filename") or path.name,
        media_type=image.get("content_type") or "application/octet-stream",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over streamable HTTP."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
