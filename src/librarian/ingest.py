"""Legacy ingest module - DEPRECATED.

Use librarian-intake instead, which provides unified intake for:
- PDFs/EPUBs from intake/ebooks/
- Kindle files from intake/kindle/{serial}/
- Kindle for Mac books

This module is kept for backward compatibility only.
"""

import subprocess
import sys
import warnings
from pathlib import Path

from librarian.config import expand_path, load_config


def ingest(source: Path, library: Path, dry_run: bool = False) -> None:
    """Add books from source path to Calibre library.

    DEPRECATED: Use librarian-intake instead.
    """
    warnings.warn(
        "ingest() is deprecated. Use librarian-intake for unified intake.",
        DeprecationWarning,
        stacklevel=2,
    )

    if not source.exists():
        print(f"Source path does not exist: {source}")
        return

    # Standard recursive add for regular sources (PDFs, EPUBs)
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

    # Use kindle_intake_path if configured, fallback to kindle_source_path
    kindle_base = config.get("kindle_intake_path") or config.get(
        "kindle_source_path", "~/data/librarian/kindle"
    )
    sync_target = expand_path(kindle_base) / serial
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
    """CLI entry point for ingest command.

    DEPRECATED: Use librarian-intake instead for unified intake.
    This command only handles PDFs/EPUBs. The new librarian-intake
    handles all formats including Kindle.
    """
    print("=" * 60)
    print("NOTICE: librarian-ingest is deprecated.")
    print("Use 'librarian-intake' for unified intake of all formats.")
    print("=" * 60)
    print()

    dry_run = "--dry-run" in sys.argv

    config = load_config()
    library = expand_path(config["library_path"])

    if not library.exists():
        print(f"Library path does not exist: {library}")
        print("Run: librarian-init to initialize the pipeline")
        sys.exit(1)

    # Ingest from intake/ebooks/ only
    intake_path = config.get("intake_path")
    if intake_path:
        path = expand_path(intake_path)
        if path.exists():
            ingest(path, library, dry_run)
        else:
            print(f"Intake path does not exist: {path}")
            print("Create it with: mkdir -p ~/data/librarian/intake/ebooks")
    else:
        print("No intake_path configured in settings.yaml")
        print("Use: librarian-intake for unified intake")


if __name__ == "__main__":
    main()
