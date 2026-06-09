#!/usr/bin/env python3
"""Backfill the books table from pgvector metadata + filesystem.

One-time migration script. Sources:
1. pgvector data_librarian_full — indexed books with chunk counts
2. Calibre directory structure — source PDFs/EPUBs and author names
3. Converted files — extracted markdown/JSON

Run: .venv/bin/python scripts/backfill_books.py
"""

import re
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from librarian.config import expand_path, load_config
from librarian.db import Book, get_session, init_db


def fix_author(char_split_author: str, calibre_author: str | None) -> str:
    """Fix the character-split author bug.

    The indexer iterated over author strings character-by-character,
    producing "A, n, d, r, e, a, s, ...". We recover from the Calibre
    directory name which has the correct author.
    """
    if calibre_author and calibre_author != "Unknown":
        return calibre_author
    # Rejoin the char-split version (every char was separated by ", ")
    if char_split_author and all(len(part) <= 1 for part in char_split_author.split(", ")):
        rejoined = "".join(char_split_author.split(", "))
        return rejoined if rejoined != "Unknown" else ""
    return char_split_author or ""


def scan_calibre_dir(calibre_path: Path) -> dict[int, dict]:
    """Scan Calibre directory structure for books.

    Returns dict keyed by book_id with author, title, source_path.
    """
    books = {}
    if not calibre_path.exists():
        return books

    for author_dir in calibre_path.iterdir():
        if not author_dir.is_dir() or author_dir.name == "metadata.db":
            continue
        author = author_dir.name

        for book_dir in author_dir.iterdir():
            if not book_dir.is_dir():
                continue
            # Extract ID from "Title (ID)" pattern
            match = re.search(r"\((\d+)\)$", book_dir.name)
            if not match:
                continue
            book_id = int(match.group(1))
            title = re.sub(r"\s*\(\d+\)$", "", book_dir.name)

            # Find source file
            source = None
            fmt = None
            for ext in ("pdf", "epub"):
                files = list(book_dir.glob(f"*.{ext}"))
                if files:
                    source = files[0]
                    fmt = ext
                    break

            books[book_id] = {
                "author": author,
                "title": title,
                "source_path": str(source) if source else None,
                "format": fmt,
            }

    return books


def scan_converted_dir(converted_path: Path) -> dict[int, str]:
    """Scan converted directory for extracted books.

    Returns dict keyed by book_id with converted_path.
    """
    result = {}
    if not converted_path.exists():
        return result

    for d in converted_path.iterdir():
        if d.is_dir() and d.name.isdigit():
            book_id = int(d.name)
            # Check there's actual content
            has_content = any(d.glob("*.md")) or any(d.glob("*.json"))
            if has_content:
                result[book_id] = str(d)

    return result


def get_pgvector_books(config: dict) -> dict[int, dict]:
    """Query pgvector for indexed books with metadata."""
    url = config["vector_store"]["pgvector_url"]
    books = {}

    with psycopg.connect(url) as conn:
        cur = conn.execute("""
            SELECT
                (metadata_->>'book_id')::int as book_id,
                metadata_->>'title' as title,
                metadata_->>'authors' as authors,
                metadata_->>'subjects' as subjects,
                metadata_->>'library' as library,
                metadata_->>'source_path' as source_path,
                count(*) as chunks
            FROM data_librarian_full
            WHERE metadata_->>'book_id' IS NOT NULL
            GROUP BY 1, 2, 3, 4, 5, 6
            ORDER BY 1
        """)

        for row in cur.fetchall():
            books[row[0]] = {
                "title": row[1],
                "authors_raw": row[2],
                "subjects": row[3],
                "library": row[4],
                "source_path": row[5],
                "chunks": row[6],
            }

    return books


def main():
    config = load_config()

    print("=== Backfilling books table ===\n")

    # 1. Create table
    print("Creating table (if needed)...")
    init_db(config)

    # 2. Check for existing data
    session = get_session(config)
    existing = session.query(Book).count()
    if existing > 0:
        print(f"  Books table already has {existing} rows. Aborting to avoid duplicates.")
        print("  Drop the table first if you want to re-run: DROP TABLE books;")
        session.close()
        return

    # 3. Gather data from all sources. The Calibre mirror path is inlined
    # here (no longer in settings) — this one-time backfill is the only
    # remaining consumer.
    calibre_path = expand_path(config.get("library_path", "~/data/librarian/calibre"))
    converted_path = expand_path(config["output_path"])

    print(f"Scanning Calibre dir: {calibre_path}")
    calibre_books = scan_calibre_dir(calibre_path)
    print(f"  Found {len(calibre_books)} books on disk")

    print(f"Scanning converted dir: {converted_path}")
    converted = scan_converted_dir(converted_path)
    print(f"  Found {len(converted)} converted books")

    print("Querying pgvector for indexed books...")
    pgvector_books = get_pgvector_books(config)
    print(f"  Found {len(pgvector_books)} indexed books")

    # 4. Merge all sources — union of all known book IDs
    all_ids = set(calibre_books.keys()) | set(pgvector_books.keys())
    print(f"\nTotal unique book IDs: {len(all_ids)}")

    # 5. Build and insert Book records
    books_to_add = []
    for book_id in sorted(all_ids):
        cal = calibre_books.get(book_id, {})
        pg = pgvector_books.get(book_id, {})
        conv_path = converted.get(book_id)

        # Title: prefer pgvector (came from original extraction), fall back to Calibre dir
        title = pg.get("title") or cal.get("title", f"Unknown (ID {book_id})")

        # Clean up XML artifacts in titles
        title = re.sub(r"<[^>]+>", "", title).strip()

        # Author: fix the char-split bug using Calibre directory name
        authors_raw = pg.get("authors_raw", "")
        calibre_author = cal.get("author")
        author = fix_author(authors_raw, calibre_author)
        authors = [author] if author and author != "Unknown" else []

        # Status: indexed > extracted > pending
        if book_id in pgvector_books:
            status = "indexed"
        elif conv_path:
            status = "extracted"
        elif cal.get("source_path"):
            status = "pending"
        else:
            status = "failed"  # no source file, likely DRM

        # Source path and format
        source = cal.get("source_path") or pg.get("source_path")
        fmt = cal.get("format") or ("pdf" if source and source.endswith(".pdf") else None)

        book = Book(
            id=book_id,
            title=title,
            authors=authors,
            format=fmt,
            status=status,
            source_path=source,
            converted_path=conv_path,
            metadata_={
                "chunks": pg.get("chunks"),
                "calibre_source_path": pg.get("source_path"),
            },
        )
        books_to_add.append(book)

    # 6. Insert
    session.add_all(books_to_add)
    session.commit()

    # 7. Fix the sequence
    from sqlalchemy import text
    max_id = max(all_ids)
    session.execute(text(f"SELECT setval('books_id_seq', {max_id})"))
    session.commit()

    # 8. Report
    print(f"\nInserted {len(books_to_add)} books:\n")
    print(f"{'ID':>4}  {'Status':<10} {'Format':<6} {'Authors':<30} {'Title':<50}")
    print("-" * 104)
    for b in books_to_add:
        author_str = ", ".join(b.authors) if b.authors else "Unknown"
        print(f"{b.id:>4}  {b.status:<10} {(b.format or '-'):<6} {author_str:<30} {b.title[:50]}")

    session.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
