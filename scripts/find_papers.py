"""List PDF books in the local Calibre library that look like academic papers.

Heuristic: filename has an Author-YYYY pattern OR file size < 5MB.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

LIBRARY = Path.home() / "data" / "librarian" / "calibre"

result = subprocess.run(
    [
        "calibredb", "list",
        "--library-path", str(LIBRARY),
        "--fields", "id,title,formats",
        "--for-machine",
    ],
    capture_output=True,
    text=True,
    check=True,
)
books = json.loads(result.stdout)

papers: list[tuple[int, str, float]] = []
for book in books:
    formats = book.get("formats", []) or []
    pdfs = [f for f in formats if f.lower().endswith(".pdf")]
    if not pdfs:
        continue
    title = book.get("title", "")
    pdf = pdfs[0]
    size_mb = os.path.getsize(pdf) / (1024 * 1024) if os.path.exists(pdf) else 0.0
    looks_like_paper = bool(re.search(r"-\d{4}-", title)) or size_mb < 5
    if looks_like_paper:
        papers.append((book["id"], title, round(size_mb, 1)))

for book_id, title, size_mb in papers:
    print(f"{book_id:>4}  {size_mb:>5.1f}MB  {title[:75]}")

print(f"--- {len(papers)} candidate papers", file=sys.stderr)
