"""File discovery for extracted book artifacts."""

from pathlib import Path
import json


MARKER_DIR = Path("raw") / "marker"


def marker_dir(book_dir: Path) -> Path:
    """Return the canonical raw Marker artifact directory for a book."""
    return book_dir / MARKER_DIR


def find_content_json(book_dir: Path) -> Path | None:
    """Find the marker content JSON for a book."""
    path = marker_dir(book_dir) / "document.json"
    return path if path.exists() else None


def find_meta_json(book_dir: Path) -> Path | None:
    """Find the marker metadata JSON for a book."""
    path = marker_dir(book_dir) / "metadata.json"
    return path if path.exists() else None


def find_markdown(book_dir: Path) -> Path | None:
    """Find the markdown file for a book."""
    path = marker_dir(book_dir) / "document.md"
    return path if path.exists() else None


def find_html(book_dir: Path) -> Path | None:
    """Find the marker HTML file for a book."""
    path = marker_dir(book_dir) / "document.html"
    return path if path.exists() else None


def load_book_content(book_dir: Path) -> dict | None:
    """Load book content blocks."""
    content_file = find_content_json(book_dir)
    if not content_file:
        return None

    with open(content_file) as f:
        data = json.load(f)

    if "blocks" not in data:
        return None

    return data
