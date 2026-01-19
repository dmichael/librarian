"""Unified intake: add books from all sources to Calibre.

Single entrypoint for:
- PDFs/EPUBs from intake/ebooks/
- Kindle files from intake/kindle/{serial}/ (physical device)

DeDRM runs automatically during Calibre import. Status tracked via Calibre columns:
- *status: imported, Status.DRM_FAILED, extracted, indexed
- *format_type: pdf, epub, kindle
- *drm_status: none, stripped, requires_manual
"""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from re import search as re_search  # For parsing calibredb output

from librarian import calibre
from librarian.calibre import Status, FormatType, DRMStatus
from librarian.config import expand_path, load_config
from librarian.drm_diagnosis import diagnose_drm_failure

# Supported file extensions by category
EBOOK_EXTENSIONS = {".pdf", ".epub"}
KINDLE_EXTENSIONS = {".azw", ".azw3", ".azw8", ".kfx", ".mobi", ".prc"}


@dataclass
class IntakeBook:
    """A book discovered for intake."""

    path: Path
    format_type: str  # "pdf", "epub", "kindle"
    source_name: str  # Human-readable source


@dataclass
class IntakeResult:
    """Result of adding a book to Calibre."""

    book: IntakeBook
    success: bool
    book_id: int | None = None
    drm_status: str | None = None  # "none", "stripped", "requires_manual"
    error: str | None = None


def discover_ebooks(intake_path: Path) -> list[IntakeBook]:
    """Find PDFs and EPUBs in the ebook intake directory."""
    if not intake_path.exists():
        return []

    books = []
    for ext in EBOOK_EXTENSIONS:
        for path in intake_path.glob(f"*{ext}"):
            format_type = "pdf" if ext == ".pdf" else "epub"
            books.append(IntakeBook(
                path=path,
                format_type=format_type,
                source_name="intake/ebooks",
            ))
        for path in intake_path.glob(f"*{ext.upper()}"):
            format_type = "pdf" if ext.lower() == ".pdf" else "epub"
            books.append(IntakeBook(
                path=path,
                format_type=format_type,
                source_name="intake/ebooks",
            ))
    return sorted(books, key=lambda b: b.path.name)


def discover_physical_kindle(kindle_path: Path, serial: str) -> list[IntakeBook]:
    """Find Kindle books from physical device (flat directory)."""
    source_dir = kindle_path / serial
    if not source_dir.exists():
        return []

    books = []
    for ext in KINDLE_EXTENSIONS:
        for path in source_dir.glob(f"*{ext}"):
            books.append(IntakeBook(
                path=path,
                format_type="kindle",
                source_name=f"kindle/{serial}",
            ))
        for path in source_dir.glob(f"*{ext.upper()}"):
            books.append(IntakeBook(
                path=path,
                format_type="kindle",
                source_name=f"kindle/{serial}",
            ))
    return sorted(books, key=lambda b: b.path.name)


def discover_books(config: dict) -> list[IntakeBook]:
    """Find all books from configured intake locations.

    Discovers from:
    - intake_path (PDFs, EPUBs): ~/data/librarian/intake/ebooks/
    - kindle_intake_path/{serial} (Kindle): ~/data/librarian/intake/kindle/{serial}/
    """
    books = []

    # Ebooks from intake/ebooks/
    intake_path = config.get("intake_path")
    if intake_path:
        books.extend(discover_ebooks(expand_path(intake_path)))

    # Kindle from intake/kindle/{serial}/
    kindle_intake = config.get("kindle_intake_path")
    serial = config.get("kindle_serial")
    if kindle_intake and serial:
        books.extend(discover_physical_kindle(expand_path(kindle_intake), serial))

    return books


def _get_all_book_ids(library_path: Path) -> set[int]:
    """Get all book IDs currently in Calibre."""
    result = subprocess.run(
        ["calibredb", "list", "--fields", "id", "--library-path", str(library_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return set()
    ids = set()
    for line in result.stdout.strip().split("\n")[1:]:
        if line.strip():
            try:
                ids.add(int(line.split()[0]))
            except (ValueError, IndexError):
                continue
    return ids


def classify_drm_result(output: str, format_type: str) -> tuple[str, str | None]:
    """Parse calibredb output to determine DRM status.

    Returns:
        Tuple of (drm_status, error_message)
        drm_status: DRMStatus.NONE, STRIPPED, or REQUIRES_MANUAL
    """
    output_lower = output.lower()

    # Check for DRM failure patterns
    if "ultimately failed to decrypt" in output_lower:
        return DRMStatus.REQUIRES_MANUAL, "DRM decryption failed"
    if "has drm and cannot be converted" in output_lower:
        return DRMStatus.REQUIRES_MANUAL, "Book still has DRM"

    # Check for successful DRM stripping
    if "decrypted" in output_lower or "drm removed" in output_lower:
        return DRMStatus.STRIPPED, None

    # No DRM indicators - assume none for PDFs/EPUBs, stripped for Kindle
    if format_type in (FormatType.PDF, FormatType.EPUB):
        return DRMStatus.NONE, None
    return DRMStatus.STRIPPED, None  # Kindle books that import without error had DRM stripped


def add_book_to_calibre(book: IntakeBook, library_path: Path) -> IntakeResult:
    """Add a single book to Calibre, detect DRM outcome."""
    before_ids = _get_all_book_ids(library_path)

    # Add the book (DeDRM runs during import)
    result = subprocess.run(
        ["calibredb", "add", str(book.path), "--library-path", str(library_path)],
        capture_output=True, text=True,
    )

    # Determine book ID
    book_id = None
    match = re_search(r"Added book ids?: (\d+)", result.stdout)
    if match:
        book_id = int(match.group(1))
    else:
        after_ids = _get_all_book_ids(library_path)
        new_ids = after_ids - before_ids
        if len(new_ids) == 1:
            book_id = new_ids.pop()
        elif len(new_ids) > 1:
            book_id = max(new_ids)

    # Check for "already exists"
    if "already exist" in result.stdout.lower():
        return IntakeResult(
            book=book,
            success=False,
            error="Book already exists in Calibre",
        )

    # Classify DRM result
    drm_status, drm_error = classify_drm_result(result.stdout, book.format_type)

    if drm_status == DRMStatus.REQUIRES_MANUAL:
        # DRM failed - diagnose and store
        source_type = "mac" if book.source_name == "Kindle for Mac" else "physical"
        diagnosis = diagnose_drm_failure(result.stdout, source_type)

        if book_id:
            calibre.set_status(book_id, Status.DRM_FAILED, library_path)
            calibre.set_format_type(book_id, book.format_type, library_path)
            calibre.set_drm_status(book_id, DRMStatus.REQUIRES_MANUAL, library_path)
            calibre.set_drm_diagnosis(book_id, diagnosis.to_dict(), library_path)

        return IntakeResult(
            book=book,
            success=False,
            book_id=book_id,
            drm_status=DRMStatus.REQUIRES_MANUAL,
            error=f"DRM failed: {diagnosis.explanation}",
        )

    if result.returncode != 0 and not book_id:
        return IntakeResult(
            book=book,
            success=False,
            error=result.stderr.strip() or result.stdout.strip() or "Unknown error",
        )

    if book_id:
        calibre.set_status(book_id, Status.IMPORTED, library_path)
        calibre.set_format_type(book_id, book.format_type, library_path)
        calibre.set_drm_status(book_id, drm_status, library_path)

    return IntakeResult(
        book=book,
        success=True,
        book_id=book_id,
        drm_status=drm_status,
    )


def intake_main():
    """CLI: librarian-intake [--dry-run] [FILE...]

    With no args, discovers from all configured intake locations.
    With FILE args, processes those specific files.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Unified intake: add books from all sources to Calibre"
    )
    parser.add_argument("--dry-run", action="store_true", help="List books without processing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("files", nargs="*", help="Specific files to process")
    args = parser.parse_args()

    config = load_config()
    library_path = calibre.get_library_path(config)

    # Discover or use provided files
    if args.files:
        books = []
        for f in args.files:
            path = Path(f).resolve()
            if not path.exists():
                print(f"File not found: {f}", file=sys.stderr)
                continue
            suffix = path.suffix.lower()
            if suffix in EBOOK_EXTENSIONS:
                format_type = "pdf" if suffix == ".pdf" else "epub"
            elif suffix in KINDLE_EXTENSIONS:
                format_type = "kindle"
            else:
                print(f"Unsupported format: {suffix}", file=sys.stderr)
                continue
            books.append(IntakeBook(path=path, format_type=format_type, source_name="command line"))
    else:
        books = discover_books(config)

    if not books:
        print("No books found to intake")
        return

    # Group by source for reporting
    by_source = {}
    for book in books:
        by_source.setdefault(book.source_name, []).append(book)

    print(f"Discovered {len(books)} books:")
    for source, source_books in by_source.items():
        print(f"  {source}: {len(source_books)}")

    if args.dry_run:
        print("\nBooks to process:")
        for source, source_books in by_source.items():
            print(f"\n{source}:")
            for book in source_books:
                print(f"  [{book.format_type}] {book.path.name}")
        return

    # Process books
    print()
    results = []
    for book in books:
        if args.verbose:
            print(f"Processing: {book.path.name}")

        result = add_book_to_calibre(book, library_path)
        results.append(result)

        if result.success:
            drm_note = f", drm:{result.drm_status}" if result.drm_status else ""
            print(f"  + [{result.book.format_type}{drm_note}] {book.path.name} -> ID {result.book_id}")
        else:
            if result.book_id:
                print(f"  ! [{result.book.format_type}] {book.path.name} -> ID {result.book_id} (drm_failed)")
            else:
                print(f"  x {book.path.name}: {result.error}")

    # Summary
    succeeded = sum(1 for r in results if r.success)
    drm_failed_count = sum(1 for r in results if r.drm_status == DRMStatus.REQUIRES_MANUAL)
    other_failed = len(results) - succeeded - drm_failed_count

    print(f"\nIntake complete: {succeeded} imported, {drm_failed_count} DRM failed, {other_failed} errors")

    if drm_failed_count > 0:
        print("\nRun 'librarian-status --failed' to see DRM failures and suggested actions.")

    sys.exit(1 if other_failed > 0 else 0)


def init_main():
    """CLI: librarian-init - Initialize or verify pipeline infrastructure."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Initialize librarian pipeline infrastructure"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    config = load_config()
    library_path = calibre.get_library_path(config)

    print("Librarian Pipeline Initialization")
    print("=" * 40)

    # 1. Create Calibre library if needed
    if not library_path.exists():
        print(f"Creating Calibre library at {library_path}")
        library_path.mkdir(parents=True, exist_ok=True)
        # Initialize by adding and removing a dummy - calibredb needs an initialized library
        print("  Note: Add your first book to initialize the library")
    else:
        print(f"Calibre library: {library_path} (exists)")

    # 2. Ensure custom columns exist
    columns = [
        ("status", "Pipeline Status", "text"),
        ("format_type", "Format Type", "text"),
        ("drm_status", "DRM Status", "text"),
        ("drm_diagnosis", "DRM Diagnosis", "text"),
        ("source_hash", "Source Hash", "text"),
        ("subjects", "Subjects", "text"),
    ]

    print("\nCustom columns:")
    for name, label, datatype in columns:
        created = calibre.ensure_custom_column(name, label, datatype, library_path)
        status = "created" if created else "exists"
        if args.verbose or created:
            print(f"  *{name}: {status}")

    # 3. Create intake directories
    intake_path = config.get("intake_path")
    kindle_intake = config.get("kindle_intake_path")

    print("\nIntake directories:")
    for name, path_str in [("intake_path", intake_path), ("kindle_intake_path", kindle_intake)]:
        if path_str:
            path = expand_path(path_str)
            path.mkdir(parents=True, exist_ok=True)
            print(f"  {path}: ok")

    # 4. Create output directory
    output_path = config.get("output_path")
    if output_path:
        path = expand_path(output_path)
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {path}: ok")

    print("\nInitialization complete!")
    print("Next: Drop files in intake directories, then run 'librarian-intake'")


def pipeline_main():
    """CLI: librarian-pipeline - Run the full pipeline.

    Runs: intake → extract → index
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the full librarian pipeline: intake → extract → index"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--skip-intake", action="store_true", help="Skip intake step")
    parser.add_argument("--skip-extract", action="store_true", help="Skip extract step")
    parser.add_argument("--skip-index", action="store_true", help="Skip index step")
    args = parser.parse_args()

    from librarian.extract import main as extract_main
    from librarian.index import main as index_main

    print("=" * 60)
    print("Librarian Pipeline")
    print("=" * 60)

    # Step 1: Intake
    if not args.skip_intake:
        print("\n[1/3] INTAKE: Adding books to Calibre")
        print("-" * 40)
        if args.dry_run:
            sys.argv = ["librarian-intake", "--dry-run"]
        else:
            sys.argv = ["librarian-intake"]
        try:
            intake_main()
        except SystemExit:
            pass  # intake_main may call sys.exit()
    else:
        print("\n[1/3] INTAKE: Skipped")

    # Step 2: Extract
    if not args.skip_extract:
        print("\n[2/3] EXTRACT: Converting to markdown")
        print("-" * 40)
        if args.dry_run:
            sys.argv = ["librarian-extract", "--dry-run"]
        else:
            sys.argv = ["librarian-extract"]
        try:
            extract_main()
        except SystemExit:
            pass
    else:
        print("\n[2/3] EXTRACT: Skipped")

    # Step 3: Index
    if not args.skip_index:
        print("\n[3/3] INDEX: Building vector store")
        print("-" * 40)
        if args.dry_run:
            print("Would index books with status='extracted'")
        else:
            sys.argv = ["librarian-index"]
            try:
                index_main()
            except SystemExit:
                pass
    else:
        print("\n[3/3] INDEX: Skipped")

    print("\n" + "=" * 60)
    print("Pipeline complete. Run 'librarian-status' to see results.")
    print("=" * 60)


if __name__ == "__main__":
    intake_main()
