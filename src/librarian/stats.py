"""Vector store and pipeline statistics.

Usage:
    librarian-stats              # Show all statistics
    librarian-stats --json       # Output as JSON for scripting
    librarian-stats --blocks     # Show block type distribution only
"""

import json
import sys
from pathlib import Path

from librarian.config import load_config
from librarian.vectorstore import get_vector_store, get_collection_names


def get_block_type_distribution(config: dict) -> dict[str, int]:
    """Get distribution of block types in the vector store."""
    store = get_vector_store(config)
    collection_names = get_collection_names(config)

    collection_name = collection_names["full"]
    counts = store.get_metadata_counts(collection_name, "block_type")
    # Sort by count descending
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def get_collection_stats(config: dict) -> dict[str, dict]:
    """Get statistics for all collections."""
    store = get_vector_store(config)
    collection_names = get_collection_names(config)

    stats = {}
    for name, collection_name in collection_names.items():
        try:
            count = store.get_collection_count(collection_name)
            stats[name] = {
                "collection_name": collection_name,
                "count": count,
            }
        except Exception as e:
            stats[name] = {
                "collection_name": collection_name,
                "count": 0,
                "error": str(e),
            }

    return stats


def get_indexed_books(config: dict) -> list[dict]:
    """Get list of books that have been indexed.

    Cross-references vector store book IDs with the books table for
    accurate author/title info.
    """
    from librarian.db import get_book_metadata

    store = get_vector_store(config)
    collection_names = get_collection_names(config)

    collection_name = collection_names["full"]
    indexed_ids = store.get_indexed_ids(collection_name)

    db_books = get_book_metadata(list(indexed_ids), config)

    books = []
    for book_id in sorted(indexed_ids):
        book = db_books.get(book_id)
        if book is not None:
            books.append({
                "book_id": book_id,
                "title": book.get("title") or "Unknown",
                "authors": ", ".join(book.get("authors") or []),
            })
        else:
            # Book no longer in the database but still indexed
            books.append({
                "book_id": book_id,
                "title": "(unknown - not in database)",
                "authors": "",
            })

    return books


def get_embedding_info(config: dict) -> dict:
    """Get embedding model configuration."""
    from librarian.config import DEFAULT_EMBED_DIM, DEFAULT_EMBED_MODEL

    embedding_config = config.get("embedding", {})
    return {
        "model": embedding_config.get("model", DEFAULT_EMBED_MODEL),
        "device": embedding_config.get("device", "cpu"),
        "dimensions": embedding_config.get("dim", DEFAULT_EMBED_DIM),
    }


def print_stats(config: dict, json_output: bool = False, blocks_only: bool = False):
    """Print all statistics."""
    if blocks_only:
        block_dist = get_block_type_distribution(config)
        if json_output:
            print(json.dumps(block_dist, indent=2))
        else:
            print("Block Type Distribution:")
            print("-" * 40)
            total = sum(block_dist.values())
            for block_type, count in block_dist.items():
                pct = (count / total * 100) if total > 0 else 0
                print(f"  {block_type:20} {count:6} ({pct:5.1f}%)")
            print("-" * 40)
            print(f"  {'Total':20} {total:6}")
        return

    # Full stats
    stats = {
        "collections": get_collection_stats(config),
        "block_types": get_block_type_distribution(config),
        "indexed_books": get_indexed_books(config),
        "embedding": get_embedding_info(config),
    }

    if json_output:
        print(json.dumps(stats, indent=2))
        return

    # Pretty print
    print("=" * 60)
    print("LIBRARIAN VECTOR STORE STATISTICS")
    print("=" * 60)

    print("\nCollections:")
    print("-" * 40)
    for name, info in stats["collections"].items():
        count = info["count"]
        coll_name = info["collection_name"]
        print(f"  {name:15} {count:6} items  ({coll_name})")

    print("\nBlock Type Distribution:")
    print("-" * 40)
    total = sum(stats["block_types"].values())
    for block_type, count in stats["block_types"].items():
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {block_type:20} {count:6} ({pct:5.1f}%)")

    print("\nIndexed Books:")
    print("-" * 40)
    for book in stats["indexed_books"]:
        title = book["title"][:40]
        authors = book["authors"][:20] if book["authors"] else ""
        print(f"  [{book['book_id']:3}] {title}")
        if authors:
            print(f"        by {authors}")

    print("\nEmbedding Model:")
    print("-" * 40)
    emb = stats["embedding"]
    print(f"  Model:      {emb['model']}")
    print(f"  Device:     {emb['device']}")
    print(f"  Dimensions: {emb['dimensions']}")

    print()


def main():
    """CLI entry point for librarian-stats."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Show vector store and pipeline statistics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  librarian-stats              # Show all statistics
  librarian-stats --json       # Output as JSON
  librarian-stats --blocks     # Show block type distribution only

Block types include: Text, Code, SectionHeader, Equation, Table, etc.
Use this to understand what content is indexed and searchable.
        """
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON for scripting"
    )
    parser.add_argument(
        "--blocks",
        action="store_true",
        help="Show block type distribution only"
    )

    args = parser.parse_args()

    config = load_config()
    print_stats(config, json_output=args.json, blocks_only=args.blocks)


def get_book_toc(config: dict, book_id: int) -> list[str]:
    """Get table of contents blocks for a specific book."""
    store = get_vector_store(config)
    collection_names = get_collection_names(config)

    collection_name = collection_names["full"]
    results = store.get_documents_by_filter(
        collection_name,
        {"book_id": book_id, "block_type": "TableOfContents"},
    )

    # Sort by page number
    results.sort(key=lambda x: x[1].get("page", 0))

    return [doc for doc, _ in results]


def toc_main():
    """CLI entry point for librarian-toc."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Show table of contents for a book",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  librarian-toc 1              # Show TOC for book ID 1
  librarian-toc --list         # List all indexed books

Use 'librarian-stats' to see all indexed books and their IDs.
        """
    )
    parser.add_argument(
        "book_id",
        nargs="?",
        type=int,
        help="Book ID to show table of contents for"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all indexed books"
    )

    args = parser.parse_args()
    config = load_config()

    if args.list:
        books = get_indexed_books(config)
        print("Indexed books:")
        print("-" * 50)
        for book in books:
            print(f"  [{book['book_id']:3}] {book['title'][:45]}")
        return

    if args.book_id is None:
        parser.print_help()
        return

    toc_blocks = get_book_toc(config, args.book_id)

    if not toc_blocks:
        print(f"No table of contents found for book ID {args.book_id}")
        print("(The book may not have a TOC or may not be indexed)")
        return

    print(f"Table of Contents (Book ID {args.book_id}):")
    print("=" * 60)
    for block in toc_blocks:
        print(block)
        print()


if __name__ == "__main__":
    main()
