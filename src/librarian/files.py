"""File discovery for extracted book artifacts."""

import json
from pathlib import Path

MARKER_DIR = Path("raw") / "marker"
PDFTOTEXT_DIR = Path("raw") / "pdftotext"


def marker_dir(book_dir: Path) -> Path:
    """Return the canonical raw Marker artifact directory for a book."""
    return book_dir / MARKER_DIR


def marker_content_json(book_dir: Path) -> Path | None:
    """Return the marker chunks JSON path for a book, if present."""
    path = marker_dir(book_dir) / "document.json"
    return path if path.exists() else None


def marker_meta_json(book_dir: Path) -> Path | None:
    """Return the marker metadata JSON path for a book, if present."""
    path = marker_dir(book_dir) / "metadata.json"
    return path if path.exists() else None


def marker_markdown(book_dir: Path) -> Path | None:
    """Return the marker-rendered markdown path for a book, if present."""
    path = marker_dir(book_dir) / "document.md"
    return path if path.exists() else None


def marker_html(book_dir: Path) -> Path | None:
    """Return the marker-rendered HTML path for a book, if present."""
    path = marker_dir(book_dir) / "document.html"
    return path if path.exists() else None


def structure_json(book_dir: Path) -> Path | None:
    """Return the indexed structure artifact path for a book, if present."""
    path = book_dir / "structure.json"
    return path if path.exists() else None


def structure_json_path(book_dir: Path) -> Path:
    """Return where the indexed structure artifact should be written."""
    return book_dir / "structure.json"


def pdftotext_dir(book_dir: Path) -> Path:
    """Return the canonical raw pdftotext artifact directory for a book."""
    return book_dir / PDFTOTEXT_DIR


def pdftotext_document(book_dir: Path) -> Path | None:
    """Return the pdftotext full-document text path for a book, if present."""
    path = pdftotext_dir(book_dir) / "document.txt"
    return path if path.exists() else None


def pdftotext_pages_json(book_dir: Path) -> Path | None:
    """Return the pdftotext per-page JSON path for a book, if present."""
    path = pdftotext_dir(book_dir) / "pages.json"
    return path if path.exists() else None


def pdftotext_meta_json(book_dir: Path) -> Path | None:
    """Return the pdftotext metadata JSON path for a book, if present."""
    path = pdftotext_dir(book_dir) / "metadata.json"
    return path if path.exists() else None


def load_extracted_blocks(book_dir: Path) -> list[dict] | None:
    """Load structured blocks from marker JSON output.

    Returns list of blocks with text and metadata, or None if not available.
    Each block contains:
        - text: Plain text content (converted from HTML)
        - html: Original block HTML (kept so equation extraction can recover
          clean LaTeX from <math> markup; marker leaves text empty for equations)
        - page: Page number
        - block_type: SectionHeader, Text, Table, etc.
        - block_id: Unique identifier

    Block list indices are persisted in the structure artifact at index time
    and resolved back to text by span reads, so any change to which blocks
    are kept or skipped here invalidates existing structure.json artifacts.
    """
    import markdownify

    content_file = marker_content_json(book_dir)
    if not content_file:
        return None

    with open(content_file) as f:
        data = json.load(f)

    # Handle both "blocks" and "chunks" keys
    raw_blocks = data.get("blocks", data.get("chunks", []))
    if not raw_blocks:
        return None

    blocks = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue

        # Convert HTML to markdown/text
        html = block.get("html", "")
        if html:
            text = markdownify.markdownify(html, heading_style="ATX").strip()
        else:
            text = block.get("text", "")

        if not text:
            continue

        blocks.append({
            "text": text,
            "html": html,
            "page": block.get("page"),
            "block_type": block.get("block_type", "Text"),
            "block_id": block.get("id", ""),
        })

    return blocks if blocks else None


def chunks_to_markdown(chunks_path: Path) -> str:
    """Convert marker chunks JSON (path) to markdown.

    The chunks format is a flat list of blocks, each with 'html' content.
    """
    import markdownify

    with open(chunks_path) as f:
        data = json.load(f)

    chunks = data if isinstance(data, list) else data.get("chunks", data.get("blocks", []))

    lines = []
    for chunk in chunks:
        if isinstance(chunk, str):
            lines.append(chunk)
        elif isinstance(chunk, dict):
            if "html" in chunk:
                md = markdownify.markdownify(chunk["html"], heading_style="ATX")
                lines.append(md.strip())
            elif "text" in chunk:
                lines.append(chunk["text"])
            elif "content" in chunk:
                lines.append(chunk["content"])

    return "\n\n".join(lines)
