"""Bulk verification of all indexed books in the library.

Runs the same checks as verify_book but in bulk via direct DB queries,
then prints a summary report grouped by status.

Usage:
    ssh ms-01 "docker exec librarian-librarian-1 python3 /app/scripts/verify_library.py"

Or locally if you have DB access:
    LIBRARIAN_DB_URL=postgresql://dmichael@ms-01.local:5432/librarian \
        .venv/bin/python scripts/verify_library.py
"""

import json
import os
import re
import sys

# Add src to path for local runs
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from librarian.config import load_config
from librarian.db import Book, get_session
from librarian.vectorstore import get_collection_names, get_vector_store


def main():
    config = load_config()
    session = get_session(config)
    store = get_vector_store(config)
    collections = get_collection_names(config)
    conn = store._get_psycopg_conn()
    table = f"data_{collections['full']}"
    eq_table = f"data_{collections['equations']}"
    ch_table = f"data_{collections['chapters']}"

    # Get all indexed books
    books = session.query(Book).filter(Book.status == "indexed").order_by(Book.id).all()
    print(f"Verifying {len(books)} indexed books...\n")

    # Bulk query: chunk counts per book
    cur = conn.execute(
        f"SELECT metadata_->>'book_id', COUNT(*) FROM {table} GROUP BY 1"
    )
    chunk_counts = {r[0]: r[1] for r in cur.fetchall()}

    # Bulk query: chapter coverage per book
    cur = conn.execute(
        f"SELECT metadata_->>'book_id', "
        f"  COUNT(*) FILTER (WHERE metadata_->>'chapter_num' IS NOT NULL "
        f"    AND metadata_->>'chapter_num' != 'null' AND metadata_->>'chapter_num' != ''), "
        f"  COUNT(*) "
        f"FROM {table} GROUP BY 1"
    )
    chapter_coverage = {}
    for bid, with_ch, total in cur.fetchall():
        chapter_coverage[bid] = (with_ch, total)

    # Bulk query: equation counts per book
    try:
        cur = conn.execute(
            f"SELECT metadata_->>'book_id', COUNT(*) FROM {eq_table} GROUP BY 1"
        )
        eq_counts = {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        eq_counts = {}

    # Bulk query: chapter summary counts per book
    try:
        cur = conn.execute(
            f"SELECT metadata_->>'book_id', COUNT(*) FROM {ch_table} GROUP BY 1"
        )
        ch_summary_counts = {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        ch_summary_counts = {}

    # Bulk query: distinct pages per book
    cur = conn.execute(
        f"SELECT metadata_->>'book_id', "
        f"  COUNT(DISTINCT (metadata_->>'page')::int) "
        f"    FILTER (WHERE metadata_->>'page' IS NOT NULL AND metadata_->>'page' != 'null'), "
        f"  MIN((metadata_->>'page')::int) "
        f"    FILTER (WHERE metadata_->>'page' IS NOT NULL AND metadata_->>'page' != 'null'), "
        f"  MAX((metadata_->>'page')::int) "
        f"    FILTER (WHERE metadata_->>'page' IS NOT NULL AND metadata_->>'page' != 'null') "
        f"FROM {table} GROUP BY 1"
    )
    page_info = {}
    for bid, distinct, mn, mx in cur.fetchall():
        page_info[bid] = (distinct, mn, mx)

    # Classify each book
    results = []
    for book in books:
        bid = str(book.id)
        chunks = chunk_counts.get(bid, 0)
        ch_with, ch_total = chapter_coverage.get(bid, (0, 0))
        ch_cov = ch_with / ch_total if ch_total else 0
        eqs = eq_counts.get(bid, 0)
        ch_summaries = ch_summary_counts.get(bid, 0)
        distinct_pages, min_page, max_page = page_info.get(bid, (0, None, None))

        issues = []

        # Metadata
        if not book.subjects:
            issues.append("no subjects")
        if not book.library:
            issues.append("no library")
        if not book.authors:
            issues.append("no authors")

        # Chunks
        if chunks == 0:
            issues.append("0 chunks in vector store")
        elif chunks < 20:
            issues.append(f"only {chunks} chunks")

        # Chapter coverage
        if ch_cov == 0 and chunks > 50:
            issues.append("0% chapter coverage")
        elif ch_cov < 0.5 and chunks > 50:
            issues.append(f"{ch_cov:.0%} chapter coverage")

        # Chapter summaries
        if ch_summaries == 0 and chunks > 100:
            issues.append("no chapter summaries")

        # Overall status
        has_red = (chunks == 0) or (ch_cov == 0 and chunks > 50)
        has_yellow = bool(issues) and not has_red
        status = "RED" if has_red else ("YELLOW" if has_yellow else "GREEN")

        results.append({
            "id": book.id,
            "title": book.title,
            "library": book.library or "",
            "subjects": book.subjects or [],
            "chunks": chunks,
            "chapter_coverage": ch_cov,
            "equations": eqs,
            "chapter_summaries": ch_summaries,
            "distinct_pages": distinct_pages,
            "page_range": f"{min_page}-{max_page}" if min_page else "?",
            "status": status,
            "issues": issues,
        })

    session.close()

    # Print report
    red = [r for r in results if r["status"] == "RED"]
    yellow = [r for r in results if r["status"] == "YELLOW"]
    green = [r for r in results if r["status"] == "GREEN"]

    print(f"{'=' * 80}")
    print(f"LIBRARY VERIFICATION REPORT")
    print(f"{'=' * 80}")
    print(f"  GREEN:  {len(green)}")
    print(f"  YELLOW: {len(yellow)}")
    print(f"  RED:    {len(red)}")
    print(f"  Total:  {len(results)}")

    if red:
        print(f"\n{'─' * 80}")
        print("RED — needs attention:")
        print(f"{'─' * 80}")
        for r in red:
            lib = f" [{r['library']}]" if r['library'] else ""
            print(f"  [{r['id']:>3}] {r['title'][:50]:<50}{lib}")
            print(f"        chunks={r['chunks']} ch_cov={r['chapter_coverage']:.0%} "
                  f"eqs={r['equations']} summaries={r['chapter_summaries']} "
                  f"pages={r['distinct_pages']} ({r['page_range']})")
            print(f"        issues: {', '.join(r['issues'])}")

    if yellow:
        print(f"\n{'─' * 80}")
        print("YELLOW — minor issues:")
        print(f"{'─' * 80}")
        for r in yellow:
            lib = f" [{r['library']}]" if r['library'] else ""
            print(f"  [{r['id']:>3}] {r['title'][:50]:<50}{lib}")
            print(f"        issues: {', '.join(r['issues'])}")

    if green:
        print(f"\n{'─' * 80}")
        print("GREEN — good:")
        print(f"{'─' * 80}")
        for r in green:
            lib = f" [{r['library']}]" if r['library'] else ""
            print(f"  [{r['id']:>3}] {r['title'][:50]:<50}{lib}")
            print(f"        chunks={r['chunks']} ch_cov={r['chapter_coverage']:.0%} "
                  f"eqs={r['equations']} summaries={r['chapter_summaries']}")


if __name__ == "__main__":
    main()
