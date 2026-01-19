"""Metadata enrichment from converted content.

Extracts title, author, publisher, year, and ISBN from converted full.json
and updates Calibre metadata.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from librarian.config import expand_path, load_config
from librarian.files import find_content_json


def _html_to_text(html: str, preserve_newlines: bool = False) -> str:
    """Convert HTML to plain text."""
    if not html:
        return ""
    text = re.sub(r'<[^>]+>', '', html)
    if preserve_newlines:
        # Collapse multiple spaces but keep newlines
        text = re.sub(r'[^\S\n]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n', text)
    else:
        text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _load_book_json(book_id: int, config: dict) -> dict | None:
    """Load the marker content JSON for a book."""
    book_dir = expand_path(config["output_path"]) / str(book_id)

    content_file = find_content_json(book_dir)
    if not content_file:
        return None

    with open(content_file) as f:
        return json.load(f)


def _get_early_pages(blocks: list, count: int = 10) -> list:
    """Get blocks from the first N pages (by page number order)."""
    pages = sorted(set(b.get("page") for b in blocks if b.get("page") is not None))
    early_pages = set(pages[:count])
    return [b for b in blocks if b.get("page") in early_pages]


def _extract_title(blocks: list) -> tuple[str | None, int | None, float]:
    """Extract title from early pages.

    Returns: (title, page_found, confidence)
    """
    # Look for SectionHeader blocks in early pages
    for b in blocks:
        if b.get("block_type") == "SectionHeader":
            text = _html_to_text(b.get("html", ""))
            # Title should be reasonably short and not look like a chapter
            if text and len(text) < 100 and not re.match(r'^\d+\.?\s', text):
                # Skip things that look like chapter headers
                if not re.match(r'^(Chapter|Section|Part)\s+\d', text, re.I):
                    return text.strip(), b.get("page"), 0.8

    # Fallback: look for bold text in early Text blocks
    pages = sorted(set(b.get("page") for b in blocks if b.get("page") is not None))
    if pages:
        first_page = pages[0]
        for b in blocks:
            if b.get("page") == first_page and b.get("block_type") == "Text":
                html = b.get("html", "")
                # Look for bold text
                bold_match = re.search(r'<b>([^<]+)</b>', html)
                if bold_match:
                    text = bold_match.group(1).strip()
                    if len(text) > 3 and len(text) < 100:
                        return text, first_page, 0.5

    return None, None, 0.0


def _extract_author(blocks: list, title_page: int | None) -> tuple[str | None, int | None, float]:
    """Extract author from near the title.

    Returns: (author, page_found, confidence)
    """
    # Look on the same page as title or nearby
    target_pages = set()
    if title_page:
        target_pages.add(title_page)

    # Also check first few pages
    pages = sorted(set(b.get("page") for b in blocks if b.get("page") is not None))
    target_pages.update(pages[:5])

    for b in blocks:
        if b.get("page") not in target_pages:
            continue
        if b.get("block_type") != "Text":
            continue

        text = _html_to_text(b.get("html", ""), preserve_newlines=True)
        first_line = text.split('\n')[0].strip()

        # Pattern: "by Author Name"
        by_match = re.search(r'\bby\s+([A-Z][a-z]+(?:\s+[A-Z]\.?\s*)?[A-Z][a-z]+)', text)
        if by_match:
            return by_match.group(1).strip(), b.get("page"), 0.9

        # Pattern: Name with department/affiliation below
        # "J. M. Selig\nDepartment of..." or "John Smith\nUniversity of..."
        if '\n' in text:
            second_line = text.split('\n')[1].strip().lower() if len(text.split('\n')) > 1 else ""
            affiliation_words = ['department', 'university', 'institute', 'college', 'school']
            if any(word in second_line for word in affiliation_words):
                # First line is likely author name
                # Match patterns like "J. M. Selig" or "John Smith"
                name_match = re.match(r'^([A-Z]\.?\s*[A-Z]?\.?\s*[A-Z][a-z]+)$', first_line)
                if name_match:
                    return first_line, b.get("page"), 0.8

        # Pattern: Just a name-like string on its own line
        # Matches "J. M. Selig", "John Smith", "J.M. Selig"
        if re.match(r'^[A-Z]\.?\s*[A-Z]?\.?\s*[A-Z][a-z]+$', first_line):
            return first_line, b.get("page"), 0.5

    # Fallback: check copyright page for "Author, F. M." pattern (cataloging format)
    for b in blocks:
        text = _html_to_text(b.get("html", ""))
        # Pattern: "Selig, J. M." (cataloging format)
        cat_match = re.match(r'^([A-Z][a-z]+),\s+([A-Z]\.?\s*[A-Z]?\.?)$', text.strip())
        if cat_match:
            # Convert "Selig, J. M." to "J. M. Selig"
            surname, initials = cat_match.groups()
            author = f"{initials.strip()} {surname}"
            return author, b.get("page"), 0.7

    return None, None, 0.0


def _extract_copyright_info(blocks: list) -> dict:
    """Extract publisher, year, ISBN from copyright page.

    Returns: {publisher, year, isbn, page, confidence}
    """
    result = {
        "publisher": None,
        "year": None,
        "isbn": None,
        "page": None,
        "confidence": 0.0,
    }

    # Search all blocks for copyright indicators
    for b in blocks:
        text = _html_to_text(b.get("html", ""))
        text_lower = text.lower()

        # Look for copyright symbol or word
        if '©' in text or 'copyright' in text_lower:
            result["page"] = b.get("page")

            # Extract year from copyright line - may have text between © and year
            # e.g., "© Prentice Hall International (UK) Ltd, 1992"
            year_match = re.search(r'(?:©|copyright)[^0-9]*(\d{4})', text_lower)
            if year_match:
                result["year"] = int(year_match.group(1))
                result["confidence"] += 0.3

        # Look for ISBN
        isbn_match = re.search(r'ISBN[-:\s]*(\d[\d-]{9,}[\dXx])', text)
        if isbn_match:
            result["isbn"] = isbn_match.group(1).replace('-', '')
            result["page"] = result["page"] or b.get("page")
            result["confidence"] += 0.3

        # Look for publisher
        if 'published by' in text_lower or 'publisher' in text_lower:
            # Try to extract publisher name
            pub_match = re.search(r'(?:published by|publisher[:\s]+)([^,\n]+)', text, re.I)
            if pub_match:
                result["publisher"] = pub_match.group(1).strip()
                result["page"] = result["page"] or b.get("page")
                result["confidence"] += 0.2

        # Known publishers
        known_publishers = [
            "Prentice Hall", "O'Reilly", "Springer", "Wiley", "McGraw-Hill",
            "Cambridge University Press", "Oxford University Press", "MIT Press",
            "Academic Press", "Addison-Wesley", "Morgan Kaufmann", "CRC Press",
        ]
        for pub in known_publishers:
            if pub.lower() in text_lower:
                result["publisher"] = pub
                result["page"] = result["page"] or b.get("page")
                result["confidence"] += 0.2
                break

    return result


def extract_metadata(book_id: int, config: dict = None) -> dict:
    """Extract metadata from converted full.json.

    Args:
        book_id: Calibre book ID
        config: Optional config dict

    Returns:
        {
            "book_id": int,
            "title": str | None,
            "authors": str | None,
            "publisher": str | None,
            "year": int | None,
            "isbn": str | None,
            "confidence": float,
            "source_pages": [int, ...],
        }
    """
    if config is None:
        config = load_config()

    data = _load_book_json(book_id, config)
    if not data:
        return {"book_id": book_id, "error": "full.json not found", "confidence": 0.0}

    blocks = data.get("blocks", [])
    early_blocks = _get_early_pages(blocks, count=10)

    # Extract each field
    title, title_page, title_conf = _extract_title(early_blocks)
    author, author_page, author_conf = _extract_author(early_blocks, title_page)
    copyright_info = _extract_copyright_info(blocks)

    # Collect source pages
    source_pages = []
    if title_page:
        source_pages.append(title_page)
    if author_page and author_page not in source_pages:
        source_pages.append(author_page)
    if copyright_info["page"] and copyright_info["page"] not in source_pages:
        source_pages.append(copyright_info["page"])

    # Calculate overall confidence
    confidence = (title_conf + author_conf + copyright_info["confidence"]) / 3

    return {
        "book_id": book_id,
        "title": title,
        "authors": author,
        "publisher": copyright_info["publisher"],
        "year": copyright_info["year"],
        "isbn": copyright_info["isbn"],
        "confidence": round(confidence, 2),
        "source_pages": sorted(source_pages),
    }


def get_calibre_metadata(book_id: int, config: dict = None) -> dict | None:
    """Get current Calibre metadata for a book."""
    if config is None:
        config = load_config()
    library_path = expand_path(config.get("library_path", "~/data/librarian/calibre"))

    cmd = [
        "calibredb", "list",
        "--library-path", str(library_path),
        "--search", f"id:{book_id}",
        "--fields", "id,title,authors,publisher,pubdate,isbn",
        "--for-machine",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if data:
            book = data[0]
            return {
                "book_id": book.get("id"),
                "title": book.get("title"),
                "authors": book.get("authors"),
                "publisher": book.get("publisher"),
                "pubdate": book.get("pubdate"),
                "isbn": book.get("isbn"),
            }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError):
        pass
    return None


def needs_enrichment(book_id: int, config: dict = None) -> tuple[bool, list[str]]:
    """Check if book needs metadata enrichment.

    Returns: (needs_enrichment, reasons)
    """
    meta = get_calibre_metadata(book_id, config)
    if not meta:
        return True, ["no metadata found"]

    reasons = []

    # Check author
    authors = meta.get("authors", "")
    if not authors or authors == "Unknown" or authors.lower() == "unknown":
        reasons.append("author is Unknown")

    # Check title - looks like filename?
    title = meta.get("title", "")
    if "_" in title or title.endswith(".pdf") or title.endswith(".epub"):
        reasons.append("title looks like filename")

    # Check publisher
    if not meta.get("publisher"):
        reasons.append("no publisher")

    return len(reasons) > 0, reasons


def apply_metadata(book_id: int, metadata: dict, config: dict = None) -> tuple[bool, str]:
    """Update Calibre with extracted metadata.

    Returns: (success, message)
    """
    if config is None:
        config = load_config()
    library_path = expand_path(config.get("library_path", "~/data/librarian/calibre"))

    cmd = ["calibredb", "set_metadata", str(book_id), "--library-path", str(library_path)]

    # Add fields that have values
    if metadata.get("title"):
        cmd.extend(["--field", f"title:{metadata['title']}"])
    if metadata.get("authors"):
        cmd.extend(["--field", f"authors:{metadata['authors']}"])
    if metadata.get("publisher"):
        cmd.extend(["--field", f"publisher:{metadata['publisher']}"])
    if metadata.get("year"):
        cmd.extend(["--field", f"pubdate:{metadata['year']}-01-01"])
    if metadata.get("isbn"):
        # ISBN goes in identifiers field with format identifiers:isbn:XXXX
        cmd.extend(["--field", f"identifiers:isbn:{metadata['isbn']}"])

    if len(cmd) == 5:  # No fields to update
        return False, "no metadata to apply"

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, "metadata updated"
        return False, result.stderr or "calibredb failed"
    except subprocess.TimeoutExpired:
        return False, "timeout"


def enrich_book(book_id: int, config: dict = None, dry_run: bool = False,
                force: bool = False, min_confidence: float = 0.4) -> dict:
    """Full enrichment flow for one book.

    Args:
        book_id: Calibre book ID
        config: Optional config dict
        dry_run: If True, don't actually update Calibre
        force: If True, enrich even if metadata looks complete
        min_confidence: Minimum confidence to apply metadata

    Returns:
        {
            "book_id": int,
            "status": "enriched" | "skipped" | "failed" | "dry_run",
            "before": {...},
            "extracted": {...},
            "changes": [...],
            "message": str,
        }
    """
    if config is None:
        config = load_config()

    result = {
        "book_id": book_id,
        "status": "skipped",
        "before": None,
        "extracted": None,
        "changes": [],
        "message": "",
    }

    # Check if enrichment needed
    if not force:
        needs, reasons = needs_enrichment(book_id, config)
        if not needs:
            result["message"] = "metadata already complete"
            return result

    # Get current metadata
    before = get_calibre_metadata(book_id, config)
    result["before"] = before

    # Extract metadata from content
    extracted = extract_metadata(book_id, config)
    result["extracted"] = extracted

    if extracted.get("error"):
        result["status"] = "failed"
        result["message"] = extracted["error"]
        return result

    if extracted["confidence"] < min_confidence:
        result["status"] = "skipped"
        result["message"] = f"confidence too low ({extracted['confidence']})"
        return result

    # Determine what would change
    changes = []
    if extracted.get("title") and (not before or before.get("title") != extracted["title"]):
        if not before or before.get("authors") == "Unknown" or "_" in (before.get("title") or ""):
            changes.append(f"title: {before.get('title') if before else 'None'} → {extracted['title']}")
    if extracted.get("authors") and (not before or before.get("authors") in [None, "Unknown"]):
        changes.append(f"authors: {before.get('authors') if before else 'None'} → {extracted['authors']}")
    if extracted.get("publisher") and (not before or not before.get("publisher")):
        changes.append(f"publisher: {before.get('publisher') if before else 'None'} → {extracted['publisher']}")
    if extracted.get("year") and (not before or not before.get("pubdate")):
        changes.append(f"year: → {extracted['year']}")
    if extracted.get("isbn") and (not before or not before.get("isbn")):
        changes.append(f"isbn: → {extracted['isbn']}")

    result["changes"] = changes

    if not changes:
        result["status"] = "skipped"
        result["message"] = "no changes needed"
        return result

    if dry_run:
        result["status"] = "dry_run"
        result["message"] = f"would apply {len(changes)} changes"
        return result

    # Apply the metadata
    # Only apply fields that need updating
    to_apply = {}
    if extracted.get("authors") and (not before or before.get("authors") in [None, "Unknown"]):
        to_apply["authors"] = extracted["authors"]
    if extracted.get("publisher") and (not before or not before.get("publisher")):
        to_apply["publisher"] = extracted["publisher"]
    if extracted.get("year") and (not before or not before.get("pubdate")):
        to_apply["year"] = extracted["year"]
    if extracted.get("isbn") and (not before or not before.get("isbn")):
        to_apply["isbn"] = extracted["isbn"]
    # Only update title if it looks like a filename
    if extracted.get("title") and before and ("_" in (before.get("title") or "") or
                                               (before.get("title") or "").endswith(".pdf")):
        to_apply["title"] = extracted["title"]

    success, msg = apply_metadata(book_id, to_apply, config)

    if success:
        result["status"] = "enriched"
        result["message"] = f"applied {len(changes)} changes"
    else:
        result["status"] = "failed"
        result["message"] = msg

    return result


def list_books_needing_enrichment(config: dict = None) -> list[dict]:
    """List all converted books that need enrichment."""
    if config is None:
        config = load_config()
    output_path = expand_path(config["output_path"])

    books = []
    for book_dir in output_path.iterdir():
        if book_dir.is_dir() and book_dir.name.isdigit():
            if find_content_json(book_dir):
                book_id = int(book_dir.name)
                needs, reasons = needs_enrichment(book_id, config)
                if needs:
                    books.append({
                        "book_id": book_id,
                        "reasons": reasons,
                    })
    return books


def format_result(result: dict) -> str:
    """Format enrichment result for CLI output."""
    lines = []
    lines.append(f"Book ID: {result['book_id']}")
    lines.append(f"Status: {result['status']}")

    if result.get("message"):
        lines.append(f"Message: {result['message']}")

    if result.get("extracted"):
        ext = result["extracted"]
        lines.append(f"\nExtracted (confidence: {ext.get('confidence', 0)}):")
        if ext.get("title"):
            lines.append(f"  Title: {ext['title']}")
        if ext.get("authors"):
            lines.append(f"  Authors: {ext['authors']}")
        if ext.get("publisher"):
            lines.append(f"  Publisher: {ext['publisher']}")
        if ext.get("year"):
            lines.append(f"  Year: {ext['year']}")
        if ext.get("isbn"):
            lines.append(f"  ISBN: {ext['isbn']}")
        if ext.get("source_pages"):
            lines.append(f"  Source pages: {ext['source_pages']}")

    if result.get("changes"):
        lines.append(f"\nChanges:")
        for change in result["changes"]:
            lines.append(f"  • {change}")

    return "\n".join(lines)


def parse_args():
    """Parse command line arguments."""
    args = {
        "book_id": None,
        "all": False,
        "dry_run": False,
        "force": False,
        "list": False,
    }

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--book-id" and i + 1 < len(sys.argv):
            args["book_id"] = int(sys.argv[i + 1])
            i += 1
        elif arg == "--all":
            args["all"] = True
        elif arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--force":
            args["force"] = True
        elif arg == "--list":
            args["list"] = True
        elif arg in ("-h", "--help"):
            print("""Usage: librarian-enrich [OPTIONS]

Enrich Calibre metadata from converted book content.

Options:
  --book-id ID   Enrich a specific book
  --all          Enrich all books needing it
  --list         List books needing enrichment
  --dry-run      Show what would change without applying
  --force        Enrich even if metadata looks complete
  -h, --help     Show this help

Examples:
  librarian-enrich --list
  librarian-enrich --book-id 156
  librarian-enrich --book-id 156 --dry-run
  librarian-enrich --all --dry-run
  librarian-enrich --all""")
            sys.exit(0)
        else:
            print(f"Unknown argument: {arg}", file=sys.stderr)
            sys.exit(1)
        i += 1

    return args


def main():
    """CLI entry point for librarian-enrich."""
    args = parse_args()
    config = load_config()

    # Handle --list
    if args["list"]:
        books = list_books_needing_enrichment(config)
        if not books:
            print("No books need enrichment.")
            return
        print(f"Books needing enrichment: {len(books)}\n")
        for b in books:
            print(f"  [{b['book_id']}] {', '.join(b['reasons'])}")
        return

    # Handle --all
    if args["all"]:
        books = list_books_needing_enrichment(config)
        if not books:
            print("No books need enrichment.")
            return

        print(f"Processing {len(books)} books...\n")
        for b in books:
            result = enrich_book(b["book_id"], config,
                               dry_run=args["dry_run"],
                               force=args["force"])
            print(f"[{b['book_id']}] {result['status']}: {result.get('message', '')}")
            if result.get("changes") and args["dry_run"]:
                for change in result["changes"]:
                    print(f"    {change}")
        return

    # Handle --book-id
    if args["book_id"]:
        result = enrich_book(args["book_id"], config,
                           dry_run=args["dry_run"],
                           force=args["force"])
        print(format_result(result))
        return

    print("Error: --book-id, --all, or --list required", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
