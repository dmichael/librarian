"""Ingest books from source paths into Calibre library."""

import subprocess
import sys
from pathlib import Path

from librarian.config import expand_path, load_config


def ingest(source: Path, library: Path, dry_run: bool = False) -> None:
    """Add books from source path to Calibre library."""
    if not source.exists():
        print(f"Source path does not exist: {source}")
        return

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
