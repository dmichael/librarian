"""librarian index — index extracted documents into vector store.

Usage:
  librarian index [dir1 dir2 ...]        Index specific extraction directories
  librarian index                        Scan output_path, index all unindexed
  librarian index --batch                Legacy Calibre batch pipeline

Each extraction directory must contain metadata.json (written by extract).
The directory name (content hash) is used to derive a stable integer ID
for vector store metadata.

Configuration (librarian.yml):
  output_path     Extracted content location
  vector_store    Backend and collection settings
  embedding       Model and device settings
  chunking        Chunk size and overlap
"""
from __future__ import annotations

import argparse
import fcntl
import sys
from pathlib import Path

from librarian.document_metadata import METADATA_FILENAME, load_document_metadata


def _doc_id_from_hash(hash_hex: str) -> int:
    """Derive a stable integer ID from a content hash hex string."""
    return int.from_bytes(bytes.fromhex(hash_hex[:8]), "big")


def _metadata_to_index_dict(meta, doc_id: int) -> dict:
    """Convert DocumentMetadata to the dict that index_book() expects."""
    return {
        "id": doc_id,
        "title": meta.title or "Unknown",
        "authors": meta.authors,
        "tags": [],
        "publisher": meta.publisher or "",
        "subjects": [],
        "source_path": meta.source_filename,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="librarian index",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("dirs", nargs="*", type=Path, help="Extraction directories to index")
    parser.add_argument("--force", action="store_true", help="Re-index even if already indexed")
    parser.add_argument("--batch", action="store_true", help="Legacy Calibre batch pipeline")
    parser.add_argument("--book-id", type=int, action="append", default=[], help="Specific book IDs (batch only)")
    return parser.parse_args()


def _discover_extracted_dirs(output_path: Path) -> list[Path]:
    """Find all subdirectories of output_path that contain metadata.json."""
    if not output_path.is_dir():
        return []
    return sorted(
        d for d in output_path.iterdir()
        if d.is_dir() and (d / METADATA_FILENAME).exists()
    )


def _run_indexing(args: argparse.Namespace) -> None:
    from llama_index.core import Settings

    from librarian.config import expand_path, load_config
    from librarian.index import (
        index_book,
        load_extracted_blocks,
        load_extracted_book,
        setup_embedding_model,
    )
    from librarian.vectorstore import get_collection_names, get_vector_store

    config = load_config()
    output_path = expand_path(config["output_path"])

    if args.dirs:
        dirs_to_index = [Path(d).resolve() for d in args.dirs]
        for d in dirs_to_index:
            if not d.is_dir():
                print(f"{d}: not a directory, skipping", file=sys.stderr)
            elif not (d / METADATA_FILENAME).exists():
                print(f"{d}: no {METADATA_FILENAME}, skipping", file=sys.stderr)
    else:
        dirs_to_index = _discover_extracted_dirs(output_path)

    dirs_to_index = [
        d for d in dirs_to_index
        if d.is_dir() and (d / METADATA_FILENAME).exists()
    ]

    if not dirs_to_index:
        print("No extracted documents found to index")
        return

    embed_model = setup_embedding_model(config)
    Settings.embed_model = embed_model

    store = get_vector_store(config)
    collections = get_collection_names(config)
    collection = collections["full"]

    vector_store = store.get_llama_store(collection)
    equation_store = store.get_llama_store(collections["equations"])
    chapter_store = store.get_llama_store(collections["chapters"])

    indexed_in_store = set() if args.force else store.get_indexed_ids(collection)

    candidates = []
    for doc_dir in dirs_to_index:
        meta = load_document_metadata(doc_dir)
        if meta is None:
            continue

        hash_hex = doc_dir.name
        doc_id = _doc_id_from_hash(hash_hex) if len(hash_hex) == 64 else hash(hash_hex) & 0xFFFFFFFF

        if not args.force and doc_id in indexed_in_store:
            print(f"  {meta.title or hash_hex}: already indexed, skipping")
            continue

        candidates.append((doc_id, doc_dir, meta))

    if not candidates:
        print("No documents need indexing")
        return

    print(f"Found {len(candidates)} documents to index")

    total_chunks = 0
    total_equations = 0
    total_chapters = 0

    for doc_id, doc_dir, meta in candidates:
        title = meta.title or "Unknown"
        metadata = _metadata_to_index_dict(meta, doc_id)

        content, raw_content = load_extracted_book(doc_dir)
        if not content:
            print(f"  [{doc_id}] No extracted content found, skipping")
            continue

        blocks = load_extracted_blocks(doc_dir)
        source_type = "blocks" if blocks else "markdown"
        print(f"  [{doc_id}] {title}: Indexing from {source_type}...")

        if args.force:
            for coll in [collection, collections["equations"], collections["chapters"]]:
                store.delete_by_filter(coll, "book_id", doc_id)

        try:
            chunks, eq_count, ch_count = index_book(
                doc_id, content, raw_content, metadata,
                vector_store, equation_store, chapter_store, config,
                blocks=blocks,
            )
            total_chunks += chunks
            total_equations += eq_count
            total_chapters += ch_count

            parts = [f"{chunks} chunks"]
            if eq_count:
                parts.append(f"{eq_count} equations")
            if ch_count:
                parts.append(f"{ch_count} chapters")
            print(f"  [{doc_id}] {title}: Created {' + '.join(parts)}")
        except Exception as e:
            print(f"  [{doc_id}] {title}: Indexing failed: {e}", file=sys.stderr)

    print(f"\nTotal indexed: {total_chunks} chunks, {total_equations} equations, {total_chapters} chapters")


def _run_batch(args: argparse.Namespace) -> None:
    """Legacy Calibre batch pipeline."""
    from llama_index.core import Settings

    from librarian import calibre
    from librarian.calibre.index import get_calibre_metadata
    from librarian.config import expand_path, load_config
    from librarian.index import (
        index_book,
        load_extracted_blocks,
        load_extracted_book,
        setup_embedding_model,
    )
    from librarian.vectorstore import get_collection_names, get_vector_store

    config = load_config()
    library_path = expand_path(config["library_path"])
    output_path = expand_path(config["output_path"])

    calibre_metadata = get_calibre_metadata(library_path)
    if not calibre_metadata:
        print("No books found in Calibre library")
        return

    embed_model = setup_embedding_model(config)
    Settings.embed_model = embed_model

    store = get_vector_store(config)
    collections = get_collection_names(config)
    collection = collections["full"]

    vector_store = store.get_llama_store(collection)
    equation_store = store.get_llama_store(collections["equations"])
    chapter_store = store.get_llama_store(collections["chapters"])

    indexed_in_store = set() if args.force else store.get_indexed_ids(collection)

    extracted_dirs = [d for d in output_path.iterdir() if d.is_dir() and d.name.isdigit()]

    books_to_index = []
    for book_dir in sorted(extracted_dirs, key=lambda d: int(d.name)):
        book_id = int(book_dir.name)
        if args.book_id and book_id not in args.book_id:
            continue
        metadata = calibre_metadata.get(book_id, {})
        status = metadata.get("*status")
        if not args.force:
            if book_id in indexed_in_store:
                continue
            if status not in (None, "extracted"):
                continue
        books_to_index.append((book_id, book_dir, metadata))

    if not books_to_index:
        print("No books need indexing")
        return

    print(f"Found {len(books_to_index)} books to index")

    total_chunks = 0
    total_equations = 0
    total_chapters = 0

    for book_id, book_dir, metadata in books_to_index:
        title = metadata.get("title", "Unknown")
        metadata["id"] = book_id

        content, raw_content = load_extracted_book(book_dir)
        if not content:
            print(f"[{book_id}] No extracted content found, skipping")
            continue

        blocks = load_extracted_blocks(book_dir)
        source_type = "blocks" if blocks else "markdown"
        print(f"[{book_id}] {title}: Indexing from {source_type}...")

        if args.force:
            for coll in [collection, collections["equations"], collections["chapters"]]:
                store.delete_by_filter(coll, "book_id", book_id)

        try:
            chunks, eq_count, ch_count = index_book(
                book_id, content, raw_content, metadata,
                vector_store, equation_store, chapter_store, config,
                blocks=blocks,
            )
            total_chunks += chunks
            total_equations += eq_count
            total_chapters += ch_count

            calibre.set_status(book_id, "indexed", library_path)

            parts = [f"{chunks} chunks"]
            if eq_count:
                parts.append(f"{eq_count} equations")
            if ch_count:
                parts.append(f"{ch_count} chapters")
            print(f"[{book_id}] {title}: Created {' + '.join(parts)}")
        except Exception as e:
            print(f"[{book_id}] {title}: Indexing failed: {e}", file=sys.stderr)

    print(f"\nTotal indexed: {total_chunks} chunks, {total_equations} equations, {total_chapters} chapters")


QDRANT_LOCK = Path("/tmp/librarian-qdrant.lock")


def main() -> None:
    args = parse_args()

    from librarian.config import load_config
    from librarian.vectorstore import get_vector_store

    config = load_config()
    store = get_vector_store(config)

    run_fn = _run_batch if args.batch else _run_indexing

    if store.requires_lock():
        with open(QDRANT_LOCK, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                run_fn(args)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    else:
        run_fn(args)
