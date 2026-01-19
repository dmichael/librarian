"""Calibre database operations.

Centralizes all Calibre CLI interactions for pipeline state tracking.

Pipeline States (*status column):
    - imported: Book added to Calibre, ready for extraction
    - drm_failed: DRM removal failed, needs manual intervention
    - extracted: Content extracted to markdown, ready for indexing
    - indexed: Indexed in vector store, searchable

Format Types (*format_type column):
    - pdf: PDF file from intake/ebooks/
    - epub: EPUB file from intake/ebooks/
    - kindle: Kindle file from intake/kindle/{serial}/

DRM Status (*drm_status column):
    - none: No DRM (PDFs, EPUBs)
    - stripped: DRM successfully removed
    - requires_manual: DRM removal failed, needs screenshot workflow
"""

import json
import subprocess
from pathlib import Path

from librarian.config import expand_path, load_config


# === Pipeline State Constants ===

class Status:
    """Valid values for *status column."""
    IMPORTED = "imported"
    DRM_FAILED = "drm_failed"
    EXTRACTED = "extracted"
    INDEXED = "indexed"

    ALL = {IMPORTED, DRM_FAILED, EXTRACTED, INDEXED}


class FormatType:
    """Valid values for *format_type column."""
    PDF = "pdf"
    EPUB = "epub"
    KINDLE = "kindle"

    ALL = {PDF, EPUB, KINDLE}


class DRMStatus:
    """Valid values for *drm_status column."""
    NONE = "none"
    STRIPPED = "stripped"
    REQUIRES_MANUAL = "requires_manual"

    ALL = {NONE, STRIPPED, REQUIRES_MANUAL}


def _run_calibredb(args: list[str], library_path: Path) -> subprocess.CompletedProcess:
    """Run a calibredb command."""
    cmd = ["calibredb"] + args + ["--library-path", str(library_path)]
    return subprocess.run(cmd, capture_output=True, text=True)


def get_library_path(config: dict | None = None) -> Path:
    """Get the Calibre library path from config."""
    if config is None:
        config = load_config()
    return expand_path(config["library_path"])


def set_custom(book_id: int, column: str, value: str, library_path: Path) -> bool:
    """Set a custom column value."""
    result = _run_calibredb(
        ["set_custom", column, str(book_id), value],
        library_path,
    )
    return result.returncode == 0


def get_custom(book_id: int, column: str, library_path: Path) -> str | None:
    """Get a custom column value."""
    result = _run_calibredb(
        ["list", "--fields", f"id,*{column}", "--for-machine", "--search", f"id:{book_id}"],
        library_path,
    )
    if result.returncode != 0:
        return None
    try:
        books = json.loads(result.stdout)
        if books:
            return books[0].get(f"*{column}")
    except json.JSONDecodeError:
        pass
    return None


def set_status(book_id: int, status: str, library_path: Path) -> bool:
    """Set pipeline status for a book.

    Valid statuses: drm_failed, imported, extracted, indexed
    """
    return set_custom(book_id, "status", status, library_path)


def get_status(book_id: int, library_path: Path) -> str | None:
    """Get pipeline status for a book."""
    return get_custom(book_id, "status", library_path)


def set_drm_diagnosis(book_id: int, diagnosis: dict, library_path: Path) -> bool:
    """Set DRM diagnosis for a failed book."""
    return set_custom(book_id, "drm_diagnosis", json.dumps(diagnosis), library_path)


def get_drm_diagnosis(book_id: int, library_path: Path) -> dict | None:
    """Get DRM diagnosis for a book."""
    value = get_custom(book_id, "drm_diagnosis", library_path)
    if value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return None


def set_format_type(book_id: int, format_type: str, library_path: Path) -> bool:
    """Set original format type for a book.

    Valid values: pdf, epub, kindle
    """
    return set_custom(book_id, "format_type", format_type, library_path)


def get_format_type(book_id: int, library_path: Path) -> str | None:
    """Get format type for a book."""
    return get_custom(book_id, "format_type", library_path)


def set_drm_status(book_id: int, drm_status: str, library_path: Path) -> bool:
    """Set DRM status for a book.

    Valid values: none, stripped, requires_manual
    """
    return set_custom(book_id, "drm_status", drm_status, library_path)


def get_drm_status(book_id: int, library_path: Path) -> str | None:
    """Get DRM status for a book."""
    return get_custom(book_id, "drm_status", library_path)


def ensure_custom_column(name: str, label: str, datatype: str, library_path: Path) -> bool:
    """Create custom column if it doesn't exist. Returns True if created."""
    # Check if column exists by listing columns
    result = _run_calibredb(["custom_columns", "--for-machine"], library_path)
    if result.returncode == 0:
        try:
            columns = json.loads(result.stdout)
            if name in columns:
                return False  # Already exists
        except json.JSONDecodeError:
            pass

    # Create the column
    result = _run_calibredb(["add_custom_column", name, label, datatype], library_path)
    return result.returncode == 0


def get_books_needing_attention(library_path: Path) -> dict:
    """Get books grouped by what attention they need.

    Returns dict with keys:
    - needs_extraction: Books with status='imported'
    - needs_indexing: Books with status='extracted'
    - drm_failed: Books with status='drm_failed'
    """
    books = get_all_books(
        library_path,
        fields="id,title,*status,*format_type,*drm_status,*drm_diagnosis"
    )

    result = {
        "needs_extraction": [],
        "needs_indexing": [],
        "drm_failed": [],
    }

    for book in books:
        status = book.get("*status")
        if status == "imported":
            result["needs_extraction"].append(book)
        elif status == "extracted":
            result["needs_indexing"].append(book)
        elif status == "drm_failed":
            result["drm_failed"].append(book)

    return result


def get_books_by_status(status: str, library_path: Path) -> list[dict]:
    """Get all books with a given status."""
    result = _run_calibredb(
        ["list", "--fields", "id,title,authors,*status,*drm_diagnosis", "--for-machine", "--search", f"*status:{status}"],
        library_path,
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def get_all_books(library_path: Path, fields: str = "id,title,authors,*status") -> list[dict]:
    """Get all books with specified fields."""
    result = _run_calibredb(
        ["list", "--fields", fields, "--for-machine"],
        library_path,
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def count_by_status(library_path: Path) -> dict[str, int]:
    """Count books by status."""
    books = get_all_books(library_path, fields="id,*status")
    counts = {
        "drm_failed": 0,
        "imported": 0,
        "extracted": 0,
        "indexed": 0,
        "no_status": 0,
    }
    for book in books:
        status = book.get("*status")
        if status in counts:
            counts[status] += 1
        else:
            counts["no_status"] += 1
    return counts


def status_main():
    """CLI entry point for librarian-status command.

    Shows pipeline status from Calibre custom columns.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Show librarian pipeline status")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--failed", action="store_true", help="Show only DRM-failed items")
    parser.add_argument("--pending", action="store_true", help="Show items needing processing")
    args = parser.parse_args()

    config = load_config()
    library_path = get_library_path(config)

    if args.json:
        books = get_all_books(library_path, fields="id,title,*status,*format_type,*drm_status,*drm_diagnosis")
        if args.failed:
            books = [b for b in books if b.get("*status") == "drm_failed"]
        elif args.pending:
            books = [b for b in books if b.get("*status") in (None, "imported", "extracted")]
        print(json.dumps(books, indent=2))
        return

    # Human-readable output
    counts = count_by_status(library_path)
    total = sum(counts.values())

    print("Librarian Pipeline Status")
    print("=" * 50)
    print(f"Total books:     {total}")
    print()
    print("By pipeline stage:")
    print(f"  indexed:       {counts['indexed']:3}  (searchable)")
    print(f"  extracted:     {counts['extracted']:3}  (needs indexing)")
    print(f"  imported:      {counts['imported']:3}  (needs extraction)")
    print(f"  drm_failed:    {counts['drm_failed']:3}  (DRM decryption failed)")
    print(f"  no_status:     {counts['no_status']:3}  (not yet processed)")
    print()

    # Show DRM failures if requested or if there are any
    if args.failed or counts["drm_failed"] > 0:
        failed_books = get_books_by_status("drm_failed", library_path)
        if failed_books:
            print("Needs Attention:")
            print("-" * 50)
            print("[Needs Manual DRM] {} books:".format(len(failed_books)))
            for book in failed_books:
                title = book.get("title", "Unknown")[:40]
                fmt = book.get("*format_type", "?")
                drm = book.get("*drm_status", "?")
                print(f"  [{book['id']:3}] {title} ({fmt}, drm:{drm})")
                diag_str = book.get("*drm_diagnosis")
                if diag_str:
                    try:
                        diag = json.loads(diag_str)
                        if diag.get("action"):
                            print(f"        -> Action: {diag['action']}")
                    except json.JSONDecodeError:
                        pass
            print()

    # Show pending items if requested
    if args.pending:
        attention = get_books_needing_attention(library_path)

        if attention["needs_extraction"]:
            print(f"[Needs Extraction] {len(attention['needs_extraction'])} books:")
            for book in attention["needs_extraction"][:10]:
                title = book.get("title", "Unknown")[:40]
                fmt = book.get("*format_type", "?")
                drm = book.get("*drm_status", "")
                drm_note = f", drm:{drm}" if drm and drm != "none" else ""
                print(f"  [{book['id']:3}] {title} ({fmt}{drm_note})")
            if len(attention["needs_extraction"]) > 10:
                print(f"  ... and {len(attention['needs_extraction']) - 10} more")
            print()

        if attention["needs_indexing"]:
            print(f"[Needs Indexing] {len(attention['needs_indexing'])} books:")
            for book in attention["needs_indexing"][:10]:
                title = book.get("title", "Unknown")[:40]
                print(f"  [{book['id']:3}] {title}")
            if len(attention["needs_indexing"]) > 10:
                print(f"  ... and {len(attention['needs_indexing']) - 10} more")
            print()

        # Also show books with no status
        no_status = [b for b in get_all_books(library_path, "id,title,*status") if not b.get("*status")]
        if no_status:
            print(f"[No Status] {len(no_status)} books (need intake processing):")
            for book in no_status[:10]:
                title = book.get("title", "Unknown")[:40]
                print(f"  [{book['id']:3}] {title}")
            if len(no_status) > 10:
                print(f"  ... and {len(no_status) - 10} more")
