"""librarian extract — extract content from source files.

Usage:
  librarian extract file.pdf [file2.pdf ...] [-o output_dir]

Extracts each file directly to an artifacts directory — one subdirectory per
file, named by content hash. Output goes to -o dir (default: cwd).

Configuration (env vars):
  LIBRARIAN_SPARK_URL   Marker HTTP service root (required for PDFs)
  GROBID_BASE_URL       GROBID service root (required for PDFs)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from librarian.document_metadata import content_hash_hex
from librarian.extract import extract


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="librarian extract",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="+", type=Path, help="Source files to extract")
    parser.add_argument("-o", "--output-dir", type=Path, help="Output directory (default: cwd)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be extracted")
    return parser.parse_args()


def _extract_files(args: argparse.Namespace) -> None:
    output_dir = args.output_dir or Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    partial = 0
    failed = 0

    for source in args.files:
        source = Path(source).resolve()
        if not source.exists():
            print(f"{source}: not found, skipping", file=sys.stderr)
            failed += 1
            continue

        hash_hex = content_hash_hex(source)
        book_dir = output_dir / hash_hex
        book_dir.mkdir(parents=True, exist_ok=True)
        print(f"{source.name} → {book_dir}", flush=True)

        if args.dry_run:
            continue

        errors, meta = extract(source, book_dir)
        if meta.title:
            print(f"  metadata: {meta.title} ({', '.join(meta.authors) or 'no authors'})")
        if not errors:
            succeeded += 1
        elif len(errors) == 1 and source.suffix.lower() == ".pdf":
            partial += 1
        else:
            failed += 1

    if not args.dry_run:
        parts = [f"{succeeded} succeeded"]
        if partial:
            parts.append(f"{partial} partial")
        if failed:
            parts.append(f"{failed} failed")
        print(f"\n{', '.join(parts)}")


def main() -> None:
    _extract_files(parse_args())
