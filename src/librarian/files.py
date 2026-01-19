"""File discovery for marker output."""

from pathlib import Path
import json


def find_content_json(book_dir: Path) -> Path | None:
    """Find the marker content JSON for a book."""
    book_id = book_dir.name
    path = book_dir / f"{book_id}.json"
    return path if path.exists() else None


def find_meta_json(book_dir: Path) -> Path | None:
    """Find the marker metadata JSON for a book."""
    book_id = book_dir.name
    path = book_dir / f"{book_id}_meta.json"
    return path if path.exists() else None


def find_markdown(book_dir: Path) -> Path | None:
    """Find the markdown file for a book."""
    book_id = book_dir.name
    path = book_dir / f"{book_id}.md"
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
