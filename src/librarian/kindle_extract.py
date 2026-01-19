"""Extract Kindle books: strip DRM and convert to EPUB using Calibre.

Handles two Kindle sources:
- Physical Kindle: intake/kindle/{serial}/*.kfx,azw (flat files)
- Kindle for Mac: ~/Library/.../com.amazon.Lassen/.../eBooks/{ASIN}/{UUID}/ (folders)
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from librarian.config import expand_path, load_config
from librarian.drm_diagnosis import diagnose_drm_failure
from librarian import calibre

# Kindle file extensions we can process
KINDLE_EXTENSIONS = {".azw", ".azw3", ".azw8", ".kfx", ".mobi", ".prc"}

# Pattern for Kindle ASIN folders (B followed by alphanumeric, or hex string)
ASIN_PATTERN = re.compile(r'^B[A-Z0-9]{9}$|^[A-F0-9]{32}$')


@dataclass
class KindleBook:
    """A Kindle book from any source."""

    path: Path  # File or folder to add to Calibre
    source_type: str  # "physical" or "mac"
    source_name: str  # Human-readable source description
    display_name: str  # Short name for display


@dataclass
class ExtractionResult:
    """Result of extracting a single book."""

    input_path: Path
    success: bool
    source_type: str = "unknown"
    source_name: str = ""
    output_path: Path | None = None
    error: str | None = None
    calibre_id: int | None = None


def find_physical_kindle_books(source_dir: Path) -> list[KindleBook]:
    """Find Kindle book files from physical device (flat directory)."""
    if not source_dir.exists():
        return []

    books = []
    for ext in KINDLE_EXTENSIONS:
        for path in source_dir.glob(f"*{ext}"):
            books.append(KindleBook(
                path=path,
                source_type="physical",
                source_name=f"Kindle Device ({source_dir.parent.name})",
                display_name=path.name,
            ))
        for path in source_dir.glob(f"*{ext.upper()}"):
            books.append(KindleBook(
                path=path,
                source_type="physical",
                source_name=f"Kindle Device ({source_dir.parent.name})",
                display_name=path.name,
            ))
    return sorted(books, key=lambda b: b.display_name)


def find_mac_kindle_books(source_dir: Path) -> list[KindleBook]:
    """Find Kindle books from Kindle for Mac (ASIN folder structure)."""
    if not source_dir.exists():
        return []

    books = []
    for asin_dir in source_dir.iterdir():
        if not asin_dir.is_dir():
            continue
        # Skip samples, plugins, and non-book folders
        if asin_dir.name.endswith("-sample"):
            continue
        if asin_dir.name.startswith("com."):
            continue
        if not ASIN_PATTERN.match(asin_dir.name):
            continue

        # Find the UUID subfolder containing book content
        for uuid_dir in asin_dir.iterdir():
            if not uuid_dir.is_dir():
                continue
            # Check for KFX manifest (downloaded book)
            if (uuid_dir / "BookManifest.kfx").exists():
                books.append(KindleBook(
                    path=uuid_dir,
                    source_type="mac",
                    source_name="Kindle for Mac",
                    display_name=f"{asin_dir.name}",
                ))
                break
            # Check for AZW files
            if list(uuid_dir.glob("*.azw*")):
                books.append(KindleBook(
                    path=uuid_dir,
                    source_type="mac",
                    source_name="Kindle for Mac",
                    display_name=f"{asin_dir.name}",
                ))
                break
    return sorted(books, key=lambda b: b.display_name)


def find_all_kindle_books(
    physical_dir: Path | None,
    mac_dir: Path | None,
) -> list[KindleBook]:
    """Find Kindle books from all configured sources."""
    books = []
    if physical_dir:
        books.extend(find_physical_kindle_books(physical_dir))
    if mac_dir:
        books.extend(find_mac_kindle_books(mac_dir))
    return books


# Legacy function for backward compatibility
def find_kindle_books(source_dir: Path) -> list[Path]:
    """Find all Kindle book files in a directory (legacy, flat files only)."""
    books = []
    for ext in KINDLE_EXTENSIONS:
        books.extend(source_dir.glob(f"*{ext}"))
        books.extend(source_dir.glob(f"*{ext.upper()}"))
    return sorted(books)


def run_calibredb(args: list[str], library_path: Path) -> subprocess.CompletedProcess:
    """Run a calibredb command."""
    cmd = ["calibredb"] + args + ["--library-path", str(library_path)]
    return subprocess.run(cmd, capture_output=True, text=True)


def search_calibre(query: str, library_path: Path) -> list[int]:
    """Search Calibre for books matching query, return list of IDs."""
    result = run_calibredb(["search", query], library_path)
    if result.returncode != 0 or not result.stdout.strip():
        return []
    # Output is comma-separated IDs
    try:
        return [int(x.strip()) for x in result.stdout.strip().split(",") if x.strip()]
    except ValueError:
        return []


def get_all_book_ids(library_path: Path) -> set[int]:
    """Get all book IDs currently in the Calibre library."""
    # Search for all books (empty string matches everything)
    result = run_calibredb(["list", "--fields", "id"], library_path)
    if result.returncode != 0:
        return set()
    # Parse output - each line after header has "id" as first column
    ids = set()
    for line in result.stdout.strip().split("\n")[1:]:  # Skip header
        if line.strip():
            try:
                ids.add(int(line.split()[0]))
            except (ValueError, IndexError):
                continue
    return ids


@dataclass
class AddResult:
    """Result of adding a book to Calibre."""

    book_id: int | None
    error: str | None
    calibre_output: str  # Raw output for diagnosis


def add_to_calibre(book_path: Path, library_path: Path) -> tuple[int | None, str | None]:
    """Add a book to Calibre, returning (book_id, error).

    Uses before/after ID comparison to safely determine the assigned ID,
    avoiding the dangerous title-search fallback that caused ID collisions.

    Books that fail DRM stay in Calibre with status='drm_failed'.
    Books that succeed get status='imported'.
    """
    # Step 1: Snapshot IDs before adding
    before_ids = get_all_book_ids(library_path)

    # Step 2: Add the book (DeDRM runs during import)
    result = run_calibredb(["add", str(book_path)], library_path)

    # Check for definite DRM failure patterns
    # DeDRM outputs "Decryption failed, trying next fallback" during attempts
    # but "Ultimately failed to decrypt" means all attempts failed
    drm_failed = False
    drm_error = ""
    if "ultimately failed to decrypt" in result.stdout.lower():
        drm_failed = True
        drm_error = f"DRM::{result.stdout}"
    elif "has drm and cannot be converted" in result.stdout.lower():
        drm_failed = True
        drm_error = f"DRM::{result.stdout}"

    # Determine book ID regardless of DRM success
    book_id = None

    # Try to parse book ID from output (most reliable when present)
    match = re.search(r"Added book ids?: (\d+)", result.stdout)
    if match:
        book_id = int(match.group(1))
    else:
        # Compare before/after IDs (safe fallback)
        after_ids = get_all_book_ids(library_path)
        new_ids = after_ids - before_ids
        if len(new_ids) == 1:
            book_id = new_ids.pop()
        elif len(new_ids) > 1:
            book_id = max(new_ids)

    if drm_failed:
        if book_id:
            # Set status to drm_failed - book stays in Calibre for tracking
            calibre.set_status(book_id, "drm_failed", library_path)
        return book_id, drm_error

    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip()

    if book_id:
        # Success - set status to imported
        calibre.set_status(book_id, "imported", library_path)
        return book_id, None

    # No new IDs - check if book already exists
    if "already exist" in result.stdout.lower():
        return None, "Book already exists in library"

    # Fail explicitly rather than guess
    return None, f"Could not determine assigned ID. Output: {result.stdout[:200]}"


def export_from_calibre(
    book_id: int, output_dir: Path, library_path: Path
) -> tuple[Path | None, str | None]:
    """Export a book from Calibre, returning (output_path, error)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Export without format filter - get whatever format exists
    args = ["export", str(book_id), "--to-dir", str(output_dir), "--single-dir"]
    result = run_calibredb(args, library_path)

    if result.returncode != 0:
        return None, result.stderr.strip()

    # Find the exported file (prefer EPUB, then others)
    for fmt in ["epub", "kfx", "kfx-zip", "azw3", "azw", "mobi", "pdf"]:
        exported = list(output_dir.glob(f"*.{fmt}"))
        if exported:
            return exported[0], None

    return None, "No output file found after export"


def convert_to_epub(input_path: Path, output_dir: Path) -> tuple[Path | None, str | None]:
    """Convert a book to EPUB using ebook-convert, returning (output_path, error)."""
    output_path = output_dir / (input_path.stem + ".epub")

    # Skip if already EPUB
    if input_path.suffix.lower() == ".epub":
        return input_path, None

    try:
        result = subprocess.run(
            ["ebook-convert", str(input_path), str(output_path)],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min timeout for large books (dictionaries, etc.)
        )
    except subprocess.TimeoutExpired:
        return None, "Conversion timed out (book may be too large)"

    if result.returncode != 0:
        # Check for DRM errors
        if "drm" in result.stderr.lower() or "protected" in result.stderr.lower():
            return None, "DRM still present - decryption failed"
        return None, f"Conversion failed: {result.stderr[:200]}"

    if output_path.exists():
        return output_path, None

    return None, "Conversion produced no output"


def remove_from_calibre(book_id: int, library_path: Path) -> str | None:
    """Remove a book from Calibre, returning error if any."""
    result = run_calibredb(["remove", str(book_id)], library_path)
    if result.returncode != 0:
        return result.stderr.strip()
    return None


def test_drm_removed(book_path: Path) -> tuple[bool, str | None]:
    """Test if a book's DRM was successfully removed by attempting conversion."""
    # Try converting to EPUB (ebook-convert will fail on DRM-protected files)
    with subprocess.Popen(
        ["ebook-convert", str(book_path), "/dev/null", "--epub-version=3"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        _, stderr = proc.communicate(timeout=60)

        if proc.returncode == 0:
            return True, None

        # Check for DRM-related errors
        if "drm" in stderr.lower() or "protected" in stderr.lower():
            return False, "DRM still present"

        # Other conversion errors aren't DRM-related
        return True, None


def extract_kindle_book(
    book: KindleBook,
    library_path: Path,
    output_dir: Path | None = None,
    keep_in_calibre: bool = True,
) -> ExtractionResult:
    """Extract a Kindle book from any source: add to Calibre (DeDRM runs during import).

    By default, books stay in Calibre after extraction. Use keep_in_calibre=False
    to export as standalone EPUB and remove from Calibre.

    Books that fail DRM stay in Calibre with status='drm_failed' for tracking.
    DRM diagnosis is stored in Calibre *drm_diagnosis column.
    """
    # Step 1: Add to Calibre (DeDRM plugin runs during import)
    # Note: book_id is returned even on DRM failure (book stays in Calibre)
    book_id, error = add_to_calibre(book.path, library_path)
    if error:
        # Check if this is a DRM failure that needs diagnosis
        if error.startswith("DRM::"):
            calibre_output = error[5:]  # Strip prefix
            diagnosis = diagnose_drm_failure(calibre_output, book.source_type)
            # Store diagnosis in Calibre for tracking
            if book_id:
                calibre.set_drm_diagnosis(book_id, diagnosis.to_dict(), library_path)
            return ExtractionResult(
                input_path=book.path,
                success=False,
                source_type=book.source_type,
                source_name=book.source_name,
                error=f"DRM decryption failed: {diagnosis.explanation}",
                calibre_id=book_id,  # Include ID even on failure for tracking
            )
        return ExtractionResult(
            input_path=book.path,
            success=False,
            source_type=book.source_type,
            source_name=book.source_name,
            error=f"Failed to add to Calibre: {error}",
            calibre_id=book_id,
        )

    # If keeping in Calibre, we're done - no need to export
    if keep_in_calibre:
        return ExtractionResult(
            input_path=book.path,
            success=True,
            source_type=book.source_type,
            source_name=book.source_name,
            output_path=None,  # Book is in Calibre, not exported
            calibre_id=book_id,
        )


def extract_book(
    book_path: Path,
    output_dir: Path | None,
    library_path: Path,
    keep_in_calibre: bool = True,
) -> ExtractionResult:
    """Legacy wrapper for extract_kindle_book."""
    book = KindleBook(
        path=book_path,
        source_type="unknown",
        source_name="Unknown",
        display_name=book_path.name,
    )
    result = extract_kindle_book(book, library_path, output_dir, keep_in_calibre)

    # Continue with export if not keeping in Calibre
    if keep_in_calibre or not result.success:
        return result

    import tempfile

    book_id = result.calibre_id

    # Step 2: Export to temp directory (only if NOT keeping in Calibre)
    # This path is for users who want standalone EPUBs
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        exported_path, error = export_from_calibre(book_id, temp_path, library_path)
        if error:
            remove_from_calibre(book_id, library_path)
            return ExtractionResult(
                input_path=book_path,
                success=False,
                error=f"Failed to export: {error}",
                calibre_id=book_id,
            )

        # Step 3: Convert to EPUB if needed
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            epub_path, error = convert_to_epub(exported_path, output_dir)
            if error:
                remove_from_calibre(book_id, library_path)
                return ExtractionResult(
                    input_path=book_path,
                    success=False,
                    error=f"Conversion failed: {error}",
                    calibre_id=book_id,
                )
        else:
            epub_path = None

    # Step 4: Remove from Calibre (since keep_in_calibre=False in this path)
    remove_from_calibre(book_id, library_path)

    return ExtractionResult(
        input_path=book_path,
        success=True,
        output_path=epub_path,
        calibre_id=None,
    )


def extract_all_unified(
    books: list[KindleBook],
    library_path: Path,
    output_dir: Path | None = None,
    keep_in_calibre: bool = True,
    verbose: bool = False,
) -> list[ExtractionResult]:
    """Extract Kindle books from any source (physical device or Mac app).

    Args:
        books: List of KindleBook objects from any source
        library_path: Calibre library path
        output_dir: Where to write extracted EPUBs (only used if keep_in_calibre=False)
        keep_in_calibre: Keep books in Calibre after extraction (default: True)
        verbose: Print detailed progress

    Returns:
        List of ExtractionResult with source tracking for clear reporting

    Note:
        Status tracking is now done via Calibre custom columns (*status).
        - Successful imports get status='imported'
        - DRM failures get status='drm_failed' with *drm_diagnosis
        Use 'librarian-status' or calibredb to query status.
    """
    if not books:
        print("No Kindle books found", flush=True)
        return []

    # Group by source for clear reporting
    by_source = {}
    for book in books:
        by_source.setdefault(book.source_name, []).append(book)

    print(f"Processing {len(books)} Kindle books:", flush=True)
    for source_name, source_books in by_source.items():
        print(f"  - {source_name}: {len(source_books)} books", flush=True)

    # Setup directories
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for book in books:
        if verbose:
            print(f"Processing [{book.source_type}]: {book.display_name}", flush=True)

        result = extract_kindle_book(book, library_path, output_dir, keep_in_calibre)
        results.append(result)

        if result.success:
            print(f"  + [{result.source_type}] {book.display_name} -> Calibre ID {result.calibre_id}", flush=True)
        else:
            print(f"  x [{result.source_type}] {book.display_name}: {result.error}", flush=True)
            if result.calibre_id:
                print(f"    (tracked in Calibre as ID {result.calibre_id} with status=drm_failed)", flush=True)

    # Summary by source
    print(f"\n{'='*60}", flush=True)
    print("Results by source:", flush=True)
    for source_name in by_source.keys():
        source_results = [r for r in results if r.source_name == source_name]
        succeeded = sum(1 for r in source_results if r.success)
        failed = len(source_results) - succeeded
        print(f"  {source_name}: {succeeded} succeeded, {failed} failed", flush=True)

    total_succeeded = sum(1 for r in results if r.success)
    total_failed = len(results) - total_succeeded
    print(f"\nTotal: {total_succeeded} succeeded, {total_failed} failed", flush=True)

    return results


# Legacy function for backward compatibility
def extract_all(
    source_dir: Path,
    output_dir: Path | None,
    library_path: Path,
    keep_in_calibre: bool = True,
    verbose: bool = False,
) -> list[ExtractionResult]:
    """Legacy extract_all - wraps extract_all_unified for single source."""
    books = [
        KindleBook(path=p, source_type="physical", source_name="Physical Kindle", display_name=p.name)
        for p in find_kindle_books(source_dir)
    ]
    return extract_all_unified(
        books, library_path, output_dir, keep_in_calibre, verbose
    )


def main():
    """CLI entry point for kindle-extract command.

    Scans both physical Kindle (intake/kindle/{serial}/) and Kindle for Mac
    and processes all found books through Calibre's DeDRM plugin.

    Status tracking is done via Calibre custom columns:
    - Successful imports: *status = 'imported'
    - DRM failures: *status = 'drm_failed', *drm_diagnosis = details

    Use 'librarian-status' to view pipeline status.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract Kindle books from all sources: strip DRM and add to Calibre"
    )
    parser.add_argument("--dry-run", action="store_true", help="List books without processing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--export-epub", action="store_true",
        help="Export standalone EPUB and remove from Calibre (default: keep in Calibre)"
    )
    parser.add_argument("--source", help="Override source directory (disables multi-source)")
    parser.add_argument("--output", help="Override output directory (only with --export-epub)")
    parser.add_argument(
        "--physical-only", action="store_true", help="Only process physical Kindle device"
    )
    parser.add_argument(
        "--mac-only", action="store_true", help="Only process Kindle for Mac"
    )
    args = parser.parse_args()

    config = load_config()
    library_path = expand_path(config["library_path"])

    # Determine sources to scan
    physical_dir = None
    mac_dir = None

    if args.source:
        # Manual override - treat as physical kindle source
        physical_dir = expand_path(args.source)
    else:
        # Physical Kindle: intake/kindle/{serial}/
        serial = config.get("kindle_serial")
        if serial and not args.mac_only:
            kindle_intake = config.get("kindle_intake_path")
            if kindle_intake:
                physical_dir = expand_path(kindle_intake) / serial
            else:
                kindle_source = config.get("kindle_source_path")
                if kindle_source:
                    physical_dir = expand_path(kindle_source) / serial

        # Kindle for Mac: ~/Library/Containers/com.amazon.Lassen/Data/Library/eBooks
        if not args.physical_only:
            mac_path = Path.home() / "Library/Containers/com.amazon.Lassen/Data/Library/eBooks"
            if mac_path.exists():
                mac_dir = mac_path

    # Output directory only needed if exporting standalone EPUBs
    keep_in_calibre = not args.export_epub
    output_dir = None
    if args.export_epub:
        if args.output:
            output_dir = expand_path(args.output)
        else:
            output_path = config.get("output_path")
            if output_path:
                output_dir = expand_path(output_path)
            else:
                print("ERROR: --export-epub requires --output or output_path in config", flush=True)
                sys.exit(1)

    # Find all books from configured sources
    books = find_all_kindle_books(physical_dir, mac_dir)

    # Print source summary
    print("Kindle Sources:", flush=True)
    if physical_dir and physical_dir.exists():
        physical_count = len(find_physical_kindle_books(physical_dir))
        print(f"  Physical ({physical_dir.parent.name}): {physical_count} books", flush=True)
    elif physical_dir:
        print(f"  Physical: not found at {physical_dir}", flush=True)
    if mac_dir and mac_dir.exists():
        mac_count = len(find_mac_kindle_books(mac_dir))
        print(f"  Kindle for Mac: {mac_count} books", flush=True)
    elif mac_dir:
        print(f"  Kindle for Mac: not found", flush=True)
    print(f"Total: {len(books)} books", flush=True)
    print(flush=True)

    if not books:
        print("No Kindle books found in any source", flush=True)
        sys.exit(0)

    if args.dry_run:
        # Group by source for clear output
        by_source = {}
        for book in books:
            by_source.setdefault(book.source_name, []).append(book)

        for source_name, source_books in by_source.items():
            print(f"\n{source_name}:", flush=True)
            for book in source_books:
                print(f"  {book.display_name}", flush=True)
        print("\nUse 'librarian-status' to see Calibre pipeline status.", flush=True)
        return

    results = extract_all_unified(
        books,
        library_path,
        output_dir=output_dir,
        keep_in_calibre=keep_in_calibre,
        verbose=args.verbose,
    )

    # Exit with non-zero if any books failed
    failed = sum(1 for r in results if not r.success)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
