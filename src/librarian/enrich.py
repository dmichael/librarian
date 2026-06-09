"""Metadata enrichment from converted content.

Extracts title, author, publisher, year, and ISBN from converted full.json
and updates the book record.
"""

import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from librarian.config import expand_path, load_config
from librarian.files import marker_content_json
from librarian.htmltext import html_to_text
from librarian.metadata import compare_authors, lookup_isbn


def _html_to_text(html: str, preserve_newlines: bool = False) -> str:
    """Convert HTML to plain text."""
    return html_to_text(html, "lines" if preserve_newlines else "flat")


def _load_book_json(book_id: int, config: dict) -> dict | None:
    """Load the marker content JSON for a book."""
    book_dir = expand_path(config["output_path"]) / str(book_id)

    content_file = marker_content_json(book_dir)
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
        book_id: Book ID
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


def get_current_metadata(book_id: int, config: dict = None) -> dict | None:
    """Get current metadata for a book from the database.

    Authors are returned as an ampersand-joined string to match the shape
    the comparison logic expects.
    """
    from librarian.db import get_book_metadata

    if config is None:
        config = load_config()

    book = get_book_metadata([book_id], config).get(book_id)
    if not book:
        return None

    year = book.get("year")
    return {
        "book_id": book_id,
        "title": book.get("title"),
        "authors": " & ".join(book.get("authors") or []),
        "publisher": book.get("publisher"),
        "pubdate": str(year) if year else None,
        "isbn": book.get("isbn"),
    }


def needs_enrichment(book_id: int, config: dict = None) -> tuple[bool, list[str]]:
    """Check if book needs metadata enrichment.

    Returns: (needs_enrichment, reasons)
    """
    meta = get_current_metadata(book_id, config)
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
    """Update the book row with extracted metadata.

    Returns: (success, message)
    """
    from librarian.db import update_book_fields

    if config is None:
        config = load_config()

    fields = {}
    if metadata.get("title"):
        fields["title"] = metadata["title"]
    if metadata.get("authors"):
        authors = metadata["authors"]
        fields["authors"] = authors if isinstance(authors, list) else [
            a.strip() for a in re.split(r"\s*&\s*", authors) if a.strip()
        ]
    if metadata.get("publisher"):
        fields["publisher"] = metadata["publisher"]
    if metadata.get("year"):
        fields["year"] = metadata["year"]
    if metadata.get("isbn"):
        fields["isbn"] = metadata["isbn"]

    if not fields:
        return False, "no metadata to apply"

    if update_book_fields(book_id, config, **fields):
        return True, "metadata updated"
    return False, f"book {book_id} not found"


def enrich_book(book_id: int, config: dict = None, dry_run: bool = False,
                force: bool = False, min_confidence: float = 0.4) -> dict:
    """Full enrichment flow for one book.

    Args:
        book_id: Book ID
        config: Optional config dict
        dry_run: If True, don't actually update the book record
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
    before = get_current_metadata(book_id, config)
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
    from librarian.db import list_extracted_book_ids

    if config is None:
        config = load_config()
    output_path = expand_path(config["output_path"])

    books = []
    for book_id in list_extracted_book_ids(config):
        if marker_content_json(output_path / str(book_id)):
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


def validate_metadata(book_id: int, config: dict = None) -> dict:
    """Cross-reference current metadata against external sources via ISBN.

    Args:
        book_id: Book ID
        config: Optional config dict

    Returns:
        {
            "book_id": int,
            "current": {"title": ..., "authors": ..., ...},
            "external": {"title": ..., "authors": ..., "source": ...},
            "discrepancies": ["authors", "title", ...],
            "recommendation": "update" | "keep" | "review",
            "error": str | None,
        }
    """
    if config is None:
        config = load_config()

    result = {
        "book_id": book_id,
        "current": None,
        "external": None,
        "discrepancies": [],
        "recommendation": "keep",
        "error": None,
    }

    # 1. Get current metadata
    current_meta = get_current_metadata(book_id, config)
    if not current_meta:
        result["error"] = "current_metadata_not_found"
        return result

    result["current"] = current_meta

    # 2. Extract ISBN from identifiers
    isbn = current_meta.get("isbn")
    if not isbn:
        result["error"] = "no_isbn"
        return result

    # 3. Lookup external metadata
    external = lookup_isbn(isbn)
    if not external:
        result["error"] = "lookup_failed"
        return result

    result["external"] = asdict(external)

    # 4. Compare fields
    discrepancies = []

    # Compare title (case-insensitive, ignore minor differences)
    current_title = (current_meta.get("title") or "").lower().strip()
    external_title = (external.title or "").lower().strip()
    if external_title and current_title != external_title:
        # Allow partial match (title might include subtitle)
        if external_title not in current_title and current_title not in external_title:
            discrepancies.append("title")

    # Compare authors
    current_authors = current_meta.get("authors", "")
    if isinstance(current_authors, str):
        current_authors = [a.strip() for a in current_authors.split("&")] if current_authors else []

    if external.authors and not compare_authors(current_authors, external.authors):
        discrepancies.append("authors")

    # Compare publisher
    current_publisher = (current_meta.get("publisher") or "").lower().strip()
    external_publisher = (external.publisher or "").lower().strip()
    if external_publisher and current_publisher and current_publisher != external_publisher:
        # Allow partial match (publisher names vary)
        if external_publisher not in current_publisher and current_publisher not in external_publisher:
            discrepancies.append("publisher")

    result["discrepancies"] = discrepancies

    # 5. Determine recommendation
    if discrepancies:
        # High confidence if ISBN matched (which it did)
        if external.confidence >= 0.9:
            result["recommendation"] = "update"
        else:
            result["recommendation"] = "review"
    else:
        result["recommendation"] = "keep"

    return result


def apply_validated_metadata(book_id: int, validation_result: dict, config: dict = None) -> tuple[bool, str]:
    """Apply external metadata to the book record for validated discrepancies.

    Only applies fields that were flagged as discrepancies.

    Args:
        book_id: Book ID
        validation_result: Result from validate_metadata()
        config: Optional config dict

    Returns:
        (success, message)
    """
    if config is None:
        config = load_config()

    if validation_result.get("error"):
        return False, f"cannot apply: {validation_result['error']}"

    if validation_result["recommendation"] == "keep":
        return False, "no discrepancies to fix"

    external = validation_result.get("external", {})
    discrepancies = validation_result.get("discrepancies", [])

    if not external or not discrepancies:
        return False, "no external data or discrepancies"

    from librarian.db import update_book_fields

    fields = {}
    # Only update fields with discrepancies
    if "title" in discrepancies and external.get("title"):
        fields["title"] = external["title"]
    if "authors" in discrepancies and external.get("authors"):
        fields["authors"] = list(external["authors"])
    if "publisher" in discrepancies and external.get("publisher"):
        fields["publisher"] = external["publisher"]

    if not fields:
        return False, "no fields to update"

    if update_book_fields(book_id, config, **fields):
        return True, f"updated: {', '.join(discrepancies)}"
    return False, f"book {book_id} not found"


def format_validation_result(result: dict) -> str:
    """Format validation result for CLI output."""
    lines = []
    lines.append(f"Book ID: {result['book_id']}")

    if result.get("error"):
        lines.append(f"Error: {result['error']}")
        return "\n".join(lines)

    current = result.get("current", {})
    external = result.get("external", {})

    lines.append(f"\nCurrent metadata:")
    lines.append(f"  Title:     {current.get('title', 'N/A')}")
    lines.append(f"  Authors:   {current.get('authors', 'N/A')}")
    lines.append(f"  Publisher: {current.get('publisher', 'N/A')}")
    lines.append(f"  ISBN:      {current.get('isbn', 'N/A')}")

    if external:
        lines.append(f"\nExternal metadata ({external.get('source', 'unknown')}):")
        lines.append(f"  Title:     {external.get('title', 'N/A')}")
        lines.append(f"  Authors:   {external.get('authors', [])}")
        lines.append(f"  Publisher: {external.get('publisher', 'N/A')}")

    discrepancies = result.get("discrepancies", [])
    recommendation = result.get("recommendation", "keep")

    if discrepancies:
        lines.append(f"\nDiscrepancies: {', '.join(discrepancies)}")
    else:
        lines.append("\nNo discrepancies found.")

    lines.append(f"Recommendation: {recommendation}")

    return "\n".join(lines)


def list_books_with_isbn(config: dict = None) -> list[int]:
    """List all books that have an ISBN for validation."""
    from librarian.db import Book, get_session

    if config is None:
        config = load_config()

    session = get_session(config)
    try:
        rows = (
            session.query(Book.id)
            .filter(Book.isbn.isnot(None), Book.isbn != "")
            .all()
        )
        return [row[0] for row in rows]
    finally:
        session.close()


def parse_validate_args():
    """Parse command line arguments for librarian-validate."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="librarian-validate",
        description="Cross-reference current metadata against external sources "
                    "(Google Books, OpenLibrary).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  librarian-validate --book-id 32
  librarian-validate --book-id 32 --fix
  librarian-validate --all
  librarian-validate --all --fix""",
    )
    parser.add_argument("--book-id", type=int, help="Validate a specific book")
    parser.add_argument("--all", action="store_true", help="Validate all books with ISBN")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-fix high-confidence discrepancies")
    return parser.parse_args()


def validate_main():
    """CLI entry point for librarian-validate."""
    args = parse_validate_args()
    config = load_config()

    if args.all:
        book_ids = list_books_with_isbn(config)
        if not book_ids:
            print("No books with ISBN found.")
            return

        print(f"Validating {len(book_ids)} books with ISBN...\n")

        stats = {"validated": 0, "discrepancies": 0, "fixed": 0, "errors": 0}

        for book_id in book_ids:
            result = validate_metadata(book_id, config)

            if result.get("error"):
                if result["error"] != "no_isbn":  # Expected for some books
                    stats["errors"] += 1
                continue

            stats["validated"] += 1
            discrepancies = result.get("discrepancies", [])

            if discrepancies:
                stats["discrepancies"] += 1
                current = result.get("current", {})
                external = result.get("external", {})

                print(f"[{book_id}] {current.get('title', 'Unknown')}")
                print(f"  Discrepancies: {', '.join(discrepancies)}")
                if "authors" in discrepancies:
                    print(f"    Current:  {current.get('authors')}")
                    print(f"    External: {external.get('authors')} ({external.get('source')})")

                if args.fix and result["recommendation"] == "update":
                    success, msg = apply_validated_metadata(book_id, result, config)
                    if success:
                        print(f"    Fixed: {msg}")
                        stats["fixed"] += 1
                    else:
                        print(f"    Fix failed: {msg}")
                print()

        print(f"\nSummary:")
        print(f"  Validated: {stats['validated']}")
        print(f"  Discrepancies: {stats['discrepancies']}")
        if args.fix:
            print(f"  Fixed: {stats['fixed']}")
        if stats["errors"]:
            print(f"  Errors: {stats['errors']}")
        return

    if args.book_id:
        result = validate_metadata(args.book_id, config)
        print(format_validation_result(result))

        if args.fix and result["recommendation"] == "update":
            print("\nApplying fix...")
            success, msg = apply_validated_metadata(args.book_id, result, config)
            if success:
                print(f"Success: {msg}")
            else:
                print(f"Failed: {msg}")
        return

    print("Error: --book-id or --all required", file=sys.stderr)
    sys.exit(1)


def parse_args():
    """Parse command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="librarian-enrich",
        description="Enrich metadata from converted book content.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  librarian-enrich --list
  librarian-enrich --book-id 156
  librarian-enrich --book-id 156 --dry-run
  librarian-enrich --all --dry-run
  librarian-enrich --all""",
    )
    parser.add_argument("--book-id", type=int, help="Enrich a specific book")
    parser.add_argument("--all", action="store_true", help="Enrich all books needing it")
    parser.add_argument("--list", action="store_true", help="List books needing enrichment")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without applying")
    parser.add_argument("--force", action="store_true",
                        help="Enrich even if metadata looks complete")
    return parser.parse_args()


def main():
    """CLI entry point for librarian-enrich."""
    args = parse_args()
    config = load_config()

    # Handle --list
    if args.list:
        books = list_books_needing_enrichment(config)
        if not books:
            print("No books need enrichment.")
            return
        print(f"Books needing enrichment: {len(books)}\n")
        for b in books:
            print(f"  [{b['book_id']}] {', '.join(b['reasons'])}")
        return

    # Handle --all
    if args.all:
        books = list_books_needing_enrichment(config)
        if not books:
            print("No books need enrichment.")
            return

        print(f"Processing {len(books)} books...\n")
        for b in books:
            result = enrich_book(b["book_id"], config,
                               dry_run=args.dry_run,
                               force=args.force)
            print(f"[{b['book_id']}] {result['status']}: {result.get('message', '')}")
            if result.get("changes") and args.dry_run:
                for change in result["changes"]:
                    print(f"    {change}")
        return

    # Handle --book-id
    if args.book_id:
        result = enrich_book(args.book_id, config,
                           dry_run=args.dry_run,
                           force=args.force)
        print(format_result(result))
        return

    print("Error: --book-id, --all, or --list required", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
