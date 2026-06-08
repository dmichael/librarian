"""librarian extract — extract content from source files.

Usage:
  librarian extract file.pdf [file2.pdf ...] [-o output_dir]
  librarian extract --batch [--force] [--book-id N ...]

With file arguments, extracts each file directly — no Calibre needed.
Output goes to -o dir (default: cwd), one subdirectory per file.

Without file arguments, --batch runs the Calibre batch pipeline.

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
    parser.add_argument("files", nargs="*", type=Path, help="Source files to extract")
    parser.add_argument("-o", "--output-dir", type=Path, help="Output directory (default: cwd)")
    parser.add_argument("--batch", action="store_true", help="Run Calibre batch pipeline")
    parser.add_argument("--cloud", action="store_true", help="Use Modal cloud GPUs (batch only)")
    parser.add_argument("--force", action="store_true", help="Re-extract even if done")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be extracted")
    parser.add_argument("--parallel", "-p", type=int, default=0, help="Max concurrent (cloud only)")
    parser.add_argument("--book-id", type=int, action="append", default=[], help="Specific book IDs (batch only)")

    args = parser.parse_args()

    if not args.files and not args.batch and not args.cloud:
        parser.error("provide files to extract, or use --batch / --cloud for the Calibre pipeline")

    return args


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


def _extract_batch(args: argparse.Namespace) -> None:
    from librarian.calibre.extract import (
        get_calibre_books,
        get_source_file,
        needs_extraction,
        update_extraction_state,
    )
    from librarian.config import expand_path, load_config

    config = load_config()
    library_path = expand_path(config["library_path"])
    output_path = expand_path(config["output_path"])
    output_path.mkdir(parents=True, exist_ok=True)

    if args.book_id:
        books = [b for b in get_calibre_books(library_path) if b["id"] in args.book_id]
    else:
        books = get_calibre_books(library_path)
        if not args.force:
            books = [b for b in books if b.get("*status") in (None, "imported")]

    if args.cloud:
        from librarian.cloud_extract import extract_books_cloud
        extract_books_cloud(
            books, library_path, output_path,
            dry_run=args.dry_run,
            max_parallel=args.parallel,
        )
        return

    if not books:
        print("No books need extraction")
        return

    print(f"Found {len(books)} books to extract")

    succeeded = 0
    failed = 0

    for book in books:
        book_id = book["id"]
        title = book.get("title", "Unknown")

        source_file, _ = get_source_file(book)
        if not source_file or not source_file.exists():
            print(f"[{book_id}] {title}: No source file found, skipping", flush=True)
            continue

        if not args.force and not needs_extraction(book, source_file, output_path):
            print(f"[{book_id}] {title}: Already extracted, skipping", flush=True)
            continue

        if args.dry_run:
            print(f"[{book_id}] {title}: Would extract from {source_file.suffix}", flush=True)
            continue

        print(f"[{book_id}] {title}: Extracting...", flush=True)

        book_output = output_path / str(book_id)
        book_output.mkdir(parents=True, exist_ok=True)

        errors, meta = extract(source_file, book_output)
        if errors:
            print(f"[{book_id}] {title}: failed: {'; '.join(errors)}", file=sys.stderr)
            failed += 1
            continue

        update_extraction_state(library_path, book_id, meta.source_hash)
        print(f"[{book_id}] {title}: Done", flush=True)
        succeeded += 1

    if not args.dry_run:
        print(f"\nExtraction complete: {succeeded} succeeded, {failed} failed")


def main() -> None:
    args = parse_args()

    if args.files:
        _extract_files(args)
    else:
        _extract_batch(args)
