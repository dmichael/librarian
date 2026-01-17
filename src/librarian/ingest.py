"""Ingest books from source paths into Calibre library."""

import re
import subprocess
import sys
from pathlib import Path

from librarian.config import expand_path, load_config

# Pattern for Kindle ASIN folders (B followed by alphanumeric, or hex string)
ASIN_PATTERN = re.compile(r'^B[A-Z0-9]{9}$|^[A-F0-9]{32}$')


def is_kindle_source(source: Path) -> bool:
    """Detect if source is a Kindle library folder."""
    # Check path for Kindle app identifier
    if "com.amazon.Lassen" in str(source):
        return True
    # Check for ASIN-named subfolders
    for child in source.iterdir():
        if child.is_dir() and ASIN_PATTERN.match(child.name):
            return True
    return False


def find_kindle_books(source: Path) -> list[Path]:
    """Find book content folders in Kindle library."""
    books = []
    for asin_dir in source.iterdir():
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
                books.append(uuid_dir)
                break
            # Check for AZW files
            if list(uuid_dir.glob("*.azw*")):
                books.append(uuid_dir)
                break
    return books


def add_to_calibre(path: Path, library: Path, dry_run: bool = False) -> bool:
    """Add a single book or folder to Calibre."""
    cmd = [
        "calibredb", "add",
        "--library-path", str(library),
        "--automerge", "ignore",
        str(path),
    ]

    if dry_run:
        print(f"  Would add: {path.name}")
        return True

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        # Check if book was actually added (not duplicate)
        if "Added" in result.stdout:
            return True
    return False


def ingest_kindle(source: Path, library: Path, dry_run: bool = False) -> None:
    """Ingest books from Kindle library."""
    print(f"Scanning Kindle library: {source}")
    books = find_kindle_books(source)
    print(f"Found {len(books)} downloaded books")

    added = 0
    for book_path in books:
        asin = book_path.parent.name
        if add_to_calibre(book_path, library, dry_run):
            added += 1
            if not dry_run:
                print(f"  Added: {asin}")

    print(f"Ingested {added} new books from Kindle")


def ingest(source: Path, library: Path, dry_run: bool = False) -> None:
    """Add books from source path to Calibre library."""
    if not source.exists():
        print(f"Source path does not exist: {source}")
        return

    # Handle Kindle sources specially
    if is_kindle_source(source):
        ingest_kindle(source, library, dry_run)
        return

    # Standard recursive add for regular sources
    cmd = [
        "calibredb", "add",
        "--library-path", str(library),
        "--automerge", "ignore",
        "-r", str(source),
    ]

    if dry_run:
        print(f"Would run: {' '.join(cmd)}")
        return

    print(f"Ingesting from {source}...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)


def kindle_sync_main():
    """CLI entry point for kindle-sync command.

    On macOS, MTP CLI tools are unreliable. This command shows the sync folder
    location and status. Use OpenMTP or Send-to-Kindle to manually copy files.

    Usage:
        librarian-kindle-sync          # Show sync folder status
        librarian-kindle-sync --path   # Just print the sync path
    """
    show_path_only = "--path" in sys.argv

    config = load_config()

    serial = config.get("kindle_serial")
    if not serial:
        print("ERROR: kindle_serial not set in config/settings.yaml")
        print("Add: kindle_serial: YOUR_KINDLE_SERIAL")
        sys.exit(1)

    # Base kindle directory (serial subfolder created automatically)
    kindle_base = expand_path("~/data/librarian/source/kindle")
    sync_target = kindle_base / serial
    sync_target.mkdir(parents=True, exist_ok=True)

    if show_path_only:
        print(sync_target)
        return

    # Count existing files
    ebook_extensions = {'.azw', '.azw3', '.azw8', '.kfx', '.mobi', '.prc'}
    ebooks = [f for f in sync_target.iterdir()
              if f.is_file() and f.suffix.lower() in ebook_extensions]
    sdr_dirs = [d for d in sync_target.iterdir()
                if d.is_dir() and d.name.endswith('.sdr')]

    print("Kindle Sync Status")
    print("=" * 60)
    print(f"Serial:      {serial}")
    print(f"Sync folder: {sync_target}")
    print(f"Ebooks:      {len(ebooks)} files")
    print(f"Metadata:    {len(sdr_dirs)} .sdr folders")
    print()

    if ebooks:
        print("Books in sync folder:")
        for f in sorted(ebooks)[:10]:
            size_mb = f.stat().st_size / 1024 / 1024
            # Truncate long names
            name = f.name[:60] + "..." if len(f.name) > 63 else f.name
            print(f"  {name} ({size_mb:.1f} MB)")
        if len(ebooks) > 10:
            print(f"  ... and {len(ebooks) - 10} more")
        print()
        print(f"Run 'librarian-ingest' to add these to Calibre.")
    else:
        print("No ebooks found in sync folder.")
        print()
        print("To sync from Kindle:")
        print("  1. Install OpenMTP: brew install --cask openmtp")
        print("  2. Connect Kindle and open OpenMTP")
        print("  3. Copy 'documents' folder contents to:")
        print(f"     {sync_target}")
        print("  4. Run: librarian-ingest")


def main():
    """CLI entry point for ingest command."""
    dry_run = "--dry-run" in sys.argv

    config = load_config()
    library = expand_path(config["library_path"])

    if not library.exists():
        print(f"Library path does not exist: {library}")
        print("Run: calibredb --library-path=... add <first-book> to initialize")
        sys.exit(1)

    for source in config["source_paths"]:
        ingest(expand_path(source), library, dry_run)


if __name__ == "__main__":
    main()
