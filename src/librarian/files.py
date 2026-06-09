"""File discovery for extracted book artifacts."""

from pathlib import Path
import json


MARKER_DIR = Path("raw") / "marker"


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
