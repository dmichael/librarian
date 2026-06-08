"""Direct content reading for agent navigation.

Provides functions to read book content by page, enabling agents to:
- Follow citations to read full page content
- Expand context around search results
- Browse available books and their structure
"""

import json
import re
import sys
from pathlib import Path

from librarian.config import expand_path, load_config
from librarian.files import marker_content_json


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text, preserving structure."""
    if not html:
        return ""
    # Remove image references
    text = re.sub(r'<img[^>]*>', '', html)
    # Convert common tags
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<p[^>]*>', '', text)
    text = re.sub(r'</p>', '\n\n', text)
    text = re.sub(r'<li[^>]*>', '  - ', text)
    text = re.sub(r'</li>', '\n', text)
    # Remove remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text


def _load_book_json(book_id: int, config: dict = None) -> dict | None:
    """Load the marker content JSON for a book."""
    if config is None:
        config = load_config()
    book_dir = expand_path(config["output_path"]) / str(book_id)

    content_file = marker_content_json(book_dir)
    if not content_file:
        return None

    with open(content_file) as f:
        return json.load(f)


def _get_book_metadata(book_ids: list[int] | None = None, config: dict = None) -> dict:
    """Get metadata for books from the database.

    Returns a dict mapping book_id -> {title, authors}.
    """
    from librarian.db import get_book_metadata

    if config is None:
        config = load_config()

    books = get_book_metadata(book_ids, config)
    return {
        bid: {
            "title": meta.get("title") or "Unknown",
            "authors": ", ".join(meta.get("authors") or []) or "Unknown",
        }
        for bid, meta in books.items()
    }


def read_page(book_id: int, page: int, config: dict = None) -> dict | None:
    """Get all content from a specific page.

    Args:
        book_id: Book ID
        page: Page number (1-indexed as in PDF)
        config: Optional config dict

    Returns:
        dict with page content or None if not found:
        {
            "book_id": int,
            "page": int,
            "title": str,
            "chunks": [{"text": str, "block_type": str}, ...]
        }
    """
    data = _load_book_json(book_id, config)
    if not data:
        return None

    blocks = data.get("blocks", [])
    page_blocks = [b for b in blocks if b.get("page") == page]

    if not page_blocks:
        return None

    # Get book title
    metadata = _get_book_metadata([book_id], config)
    title = metadata.get(book_id, {}).get("title", f"Book {book_id}")

    return {
        "book_id": book_id,
        "page": page,
        "title": title,
        "chunks": [
            {
                "text": _html_to_text(b.get("html", "")),
                "block_type": b.get("block_type", "Unknown"),
            }
            for b in page_blocks
            if b.get("html")  # Skip empty blocks
        ],
    }


def read_pages(book_id: int, start_page: int, end_page: int, config: dict = None) -> dict | None:
    """Get content from a page range.

    Args:
        book_id: Book ID
        start_page: First page (inclusive)
        end_page: Last page (inclusive)
        config: Optional config dict

    Returns:
        dict with page range content or None if not found:
        {
            "book_id": int,
            "title": str,
            "start_page": int,
            "end_page": int,
            "pages": [{"page": int, "chunks": [...]}, ...]
        }
    """
    data = _load_book_json(book_id, config)
    if not data:
        return None

    blocks = data.get("blocks", [])

    # Group blocks by page
    pages = {}
    for b in blocks:
        p = b.get("page")
        if p is not None and start_page <= p <= end_page:
            if p not in pages:
                pages[p] = []
            text = _html_to_text(b.get("html", ""))
            if text:  # Skip empty blocks
                pages[p].append({
                    "text": text,
                    "block_type": b.get("block_type", "Unknown"),
                })

    if not pages:
        return None

    # Get book title
    metadata = _get_book_metadata([book_id], config)
    title = metadata.get(book_id, {}).get("title", f"Book {book_id}")

    return {
        "book_id": book_id,
        "title": title,
        "start_page": start_page,
        "end_page": end_page,
        "pages": [{"page": p, "chunks": pages[p]} for p in sorted(pages.keys())],
    }


def get_context(book_id: int, page: int, window: int = 2, config: dict = None) -> dict | None:
    """Get content from page and surrounding pages.

    Args:
        book_id: Book ID
        page: Center page number
        window: Number of pages before and after (default 2)
        config: Optional config dict

    Returns:
        Same as read_pages, centered on the given page
    """
    return read_pages(book_id, page - window, page + window, config)


def list_books(config: dict = None) -> list[dict]:
    """List all indexed books with metadata.

    Returns:
        List of dicts: [{book_id, title, authors, page_count}, ...]
    """
    if config is None:
        config = load_config()
    output_path = expand_path(config["output_path"])

    # Find all book directories with content JSON
    book_ids = []
    page_counts = {}
    for book_dir in output_path.iterdir():
        if book_dir.is_dir() and book_dir.name.isdigit():
            content_file = marker_content_json(book_dir)
            if content_file:
                book_id = int(book_dir.name)
                book_ids.append(book_id)
                # Get page count from JSON
                try:
                    with open(content_file) as f:
                        data = json.load(f)
                    blocks = data.get("blocks", [])
                    if blocks:
                        page_counts[book_id] = max(
                            (b.get("page", 0) for b in blocks),
                            default=0
                        )
                except (json.JSONDecodeError, OSError):
                    page_counts[book_id] = 0

    if not book_ids:
        return []

    # Get metadata from the database
    metadata = _get_book_metadata(book_ids, config)

    books = []
    for book_id in sorted(book_ids):
        meta = metadata.get(book_id, {})
        books.append({
            "book_id": book_id,
            "title": meta.get("title", f"Book {book_id}"),
            "authors": meta.get("authors", "Unknown"),
            "page_count": page_counts.get(book_id, 0),
        })
    return books


def search_book(book_id: int, query: str, config: dict = None) -> dict | None:
    """Search for text within a book and return matching pages.

    Args:
        book_id: Book ID
        query: Search term (case-insensitive)
        config: Optional config dict

    Returns:
        dict with search results or None if book not found:
        {
            "book_id": int,
            "title": str,
            "query": str,
            "matches": [{"page": int, "snippets": [str, ...]}, ...]
        }
    """
    data = _load_book_json(book_id, config)
    if not data:
        return None

    blocks = data.get("blocks", [])
    query_lower = query.lower()

    # Find blocks containing the query
    matches_by_page = {}
    for b in blocks:
        text = _html_to_text(b.get("html", ""))
        if query_lower in text.lower():
            page = b.get("page", 0)
            if page not in matches_by_page:
                matches_by_page[page] = []
            # Extract snippet around match
            idx = text.lower().find(query_lower)
            start = max(0, idx - 50)
            end = min(len(text), idx + len(query) + 50)
            snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
            matches_by_page[page].append(snippet.replace("\n", " "))

    if not matches_by_page:
        return None

    # Get book title
    metadata = _get_book_metadata([book_id], config)
    title = metadata.get(book_id, {}).get("title", f"Book {book_id}")

    return {
        "book_id": book_id,
        "title": title,
        "query": query,
        "matches": [
            {"page": p, "snippets": matches_by_page[p]}
            for p in sorted(matches_by_page.keys())
        ],
    }


def find_references(book_id: int, config: dict = None) -> dict | None:
    """Find citations, bibliography, and external references in a book.

    Searches for common reference patterns:
    - Bibliography/References/Further Reading sections
    - Numbered citations like [1], [2]
    - Author-year citations like (Smith, 2020)
    - ISBN mentions
    - Known academic publishers

    Args:
        book_id: Book ID
        config: Optional config dict

    Returns:
        dict with reference analysis:
        {
            "book_id": int,
            "title": str,
            "has_bibliography": bool,
            "bibliography_pages": [int, ...],
            "citation_patterns": [{"pattern": str, "count": int, "examples": [str]}, ...],
            "isbn_mentions": [str, ...],
            "publisher_mentions": [{"publisher": str, "page": int}, ...],
        }
    """
    data = _load_book_json(book_id, config)
    if not data:
        return None

    blocks = data.get("blocks", [])

    # Get book title
    metadata = _get_book_metadata([book_id], config)
    title = metadata.get(book_id, {}).get("title", f"Book {book_id}")

    result = {
        "book_id": book_id,
        "title": title,
        "has_bibliography": False,
        "bibliography_pages": [],
        "citation_patterns": [],
        "isbn_mentions": [],
        "publisher_mentions": [],
        "searches_performed": [],
    }

    # Track what we searched for (for transparency)
    searches = []

    # 1. Look for bibliography/references sections
    bib_keywords = ["bibliography", "references", "further reading", "works cited", "citations"]
    searches.append(f"Section headers containing: {', '.join(bib_keywords)}")

    for b in blocks:
        if b.get("block_type") == "SectionHeader":
            text = _html_to_text(b.get("html", "")).lower()
            for kw in bib_keywords:
                if kw in text:
                    result["has_bibliography"] = True
                    page = b.get("page")
                    if page and page not in result["bibliography_pages"]:
                        result["bibliography_pages"].append(page)

    # 2. Look for numbered citations [1], [2], etc.
    searches.append("Numbered citations: [1], [2], etc.")
    numbered_citations = []
    for b in blocks:
        text = _html_to_text(b.get("html", ""))
        matches = re.findall(r'\[(\d{1,3})\]', text)
        numbered_citations.extend(matches)

    if numbered_citations:
        unique_nums = sorted(set(int(n) for n in numbered_citations))
        result["citation_patterns"].append({
            "pattern": "[N] numbered citations",
            "count": len(unique_nums),
            "examples": [f"[{n}]" for n in unique_nums[:5]],
        })

    # 3. Look for author-year citations (Smith, 2020) or Smith (2020)
    searches.append("Author-year citations: (Author, Year) or Author (Year)")
    author_year = []
    for b in blocks:
        text = _html_to_text(b.get("html", ""))
        # (Author, Year) pattern
        matches1 = re.findall(r'\([A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?,\s*\d{4}\)', text)
        # Author (Year) pattern
        matches2 = re.findall(r'[A-Z][a-z]+\s+\(\d{4}\)', text)
        author_year.extend(matches1 + matches2)

    if author_year:
        unique_cites = list(set(author_year))[:10]
        result["citation_patterns"].append({
            "pattern": "Author-year citations",
            "count": len(set(author_year)),
            "examples": unique_cites[:5],
        })

    # 4. Look for ISBN mentions
    searches.append("ISBN patterns")
    for b in blocks:
        text = _html_to_text(b.get("html", ""))
        isbn_matches = re.findall(r'ISBN[-:\s]*([\d-]{10,17})', text, re.I)
        for isbn in isbn_matches:
            clean_isbn = isbn.replace('-', '')
            if clean_isbn not in result["isbn_mentions"]:
                result["isbn_mentions"].append(clean_isbn)

    # 5. Look for known academic publishers (might indicate cited works)
    searches.append("Academic publisher mentions")
    publishers = [
        "Springer", "Wiley", "Elsevier", "Academic Press", "Cambridge University Press",
        "Oxford University Press", "MIT Press", "McGraw-Hill", "Pearson", "CRC Press",
        "IEEE", "ACM", "SIAM", "Addison-Wesley", "Morgan Kaufmann", "O'Reilly",
    ]

    for b in blocks:
        text = _html_to_text(b.get("html", ""))
        page = b.get("page")
        for pub in publishers:
            if pub.lower() in text.lower():
                # Check if it's not just the book's own publisher (copyright page)
                if page not in [p.get("page") for p in result["publisher_mentions"]
                               if p.get("publisher") == pub]:
                    result["publisher_mentions"].append({
                        "publisher": pub,
                        "page": page,
                    })

    result["searches_performed"] = searches
    return result


def read_first_pages(book_id: int, count: int = 5, config: dict = None) -> dict | None:
    """Read the first N pages of content from a book.

    Args:
        book_id: Book ID
        count: Number of pages to read (default 5)
        config: Optional config dict

    Returns:
        Same format as read_pages
    """
    data = _load_book_json(book_id, config)
    if not data:
        return None

    blocks = data.get("blocks", [])
    if not blocks:
        return None

    # Find all unique page numbers and sort them
    page_nums = sorted(set(b.get("page") for b in blocks if b.get("page") is not None))
    if not page_nums:
        return None

    # Take the first N pages
    first_pages = page_nums[:count]
    return read_pages(book_id, min(first_pages), max(first_pages), config)


def read_chapter(book_id: int, chapter: str | int, config: dict = None) -> dict | None:
    """Read content starting from a specific chapter.

    Args:
        book_id: Book ID
        chapter: Chapter name (fuzzy match) or number (1-indexed)
        config: Optional config dict

    Returns:
        dict with chapter content or None if not found
    """
    structure = get_book_structure(book_id, config)
    if not structure or not structure.get("chapters"):
        return None

    chapters = structure["chapters"]
    target_chapter = None

    if isinstance(chapter, int) or chapter.isdigit():
        # Numeric chapter index
        idx = int(chapter) - 1
        if 0 <= idx < len(chapters):
            target_chapter = chapters[idx]
    else:
        # Fuzzy match on chapter title
        chapter_lower = chapter.lower()
        for ch in chapters:
            if chapter_lower in ch["title"].lower():
                target_chapter = ch
                break

    if not target_chapter:
        return None

    # Find the next chapter to determine end page
    start_page = target_chapter["start_page"]
    end_page = start_page + 10  # Default: read 10 pages

    # Try to find the next chapter's start page
    for i, ch in enumerate(chapters):
        if ch["start_page"] == start_page and i + 1 < len(chapters):
            next_start = chapters[i + 1]["start_page"]
            # Only use it if it's after our start (page numbers can be weird)
            if next_start > start_page:
                end_page = next_start - 1
            break

    result = read_pages(book_id, start_page, end_page, config)
    if result:
        result["chapter_title"] = target_chapter["title"]
    return result


def get_book_structure(book_id: int, config: dict = None) -> dict | None:
    """Get table of contents / chapter structure for a book.

    Args:
        book_id: Book ID
        config: Optional config dict

    Returns:
        dict with chapter structure or None if not found:
        {
            "book_id": int,
            "title": str,
            "chapters": [{"title": str, "start_page": int}, ...]
        }
    """
    data = _load_book_json(book_id, config)
    if not data:
        return None

    blocks = data.get("blocks", [])

    # Get book title
    metadata = _get_book_metadata([book_id], config)
    title = metadata.get(book_id, {}).get("title", f"Book {book_id}")

    # Find section headers to build structure
    chapters = []
    for b in blocks:
        if b.get("block_type") == "SectionHeader":
            header_text = _html_to_text(b.get("html", ""))
            if header_text:
                chapters.append({
                    "title": header_text,
                    "start_page": b.get("page", 0),
                })

    # Deduplicate chapters with same title on same page
    seen = set()
    unique_chapters = []
    for ch in chapters:
        key = (ch["title"], ch["start_page"])
        if key not in seen:
            seen.add(key)
            unique_chapters.append(ch)

    return {
        "book_id": book_id,
        "title": title,
        "chapters": unique_chapters,
    }


def format_page_output(result: dict) -> str:
    """Format a single page result for CLI output."""
    lines = []
    lines.append(f"Book: {result['title']} (ID: {result['book_id']})")
    lines.append(f"Page: {result['page']}")
    lines.append("=" * 60)

    for chunk in result.get("chunks", []):
        block_type = chunk.get("block_type", "")
        text = chunk.get("text", "")
        if block_type in ("SectionHeader",):
            lines.append(f"\n## {text}\n")
        elif block_type == "Equation":
            lines.append(f"\n[Equation]\n{text}\n")
        else:
            lines.append(text)

    return "\n".join(lines)


def format_pages_output(result: dict) -> str:
    """Format a page range result for CLI output."""
    lines = []
    lines.append(f"Book: {result['title']} (ID: {result['book_id']})")
    lines.append(f"Pages: {result['start_page']}-{result['end_page']}")
    lines.append("=" * 60)

    for page_data in result.get("pages", []):
        lines.append(f"\n--- Page {page_data['page']} ---\n")
        for chunk in page_data.get("chunks", []):
            block_type = chunk.get("block_type", "")
            text = chunk.get("text", "")
            if block_type in ("SectionHeader",):
                lines.append(f"\n## {text}\n")
            elif block_type == "Equation":
                lines.append(f"\n[Equation]\n{text}\n")
            else:
                lines.append(text)

    return "\n".join(lines)


def format_books_output(books: list[dict]) -> str:
    """Format book list for CLI output."""
    lines = ["Available indexed books:", "=" * 60]
    for b in books:
        lines.append(
            f"[{b['book_id']:>4}] {b['title'][:50]:<50} "
            f"({b['page_count']} pages) - {b['authors'][:30]}"
        )
    return "\n".join(lines)


def format_structure_output(result: dict) -> str:
    """Format book structure for CLI output."""
    lines = []
    lines.append(f"Book: {result['title']} (ID: {result['book_id']})")
    lines.append("=" * 60)
    lines.append("Table of Contents:")

    for ch in result.get("chapters", []):
        lines.append(f"  p.{ch['start_page']:>4}  {ch['title']}")

    return "\n".join(lines)


def format_search_output(result: dict) -> str:
    """Format search results for CLI output."""
    lines = []
    lines.append(f"Book: {result['title']} (ID: {result['book_id']})")
    lines.append(f"Search: \"{result['query']}\"")
    lines.append(f"Found {len(result['matches'])} pages with matches")
    lines.append("=" * 60)

    for match in result.get("matches", []):
        lines.append(f"\n--- Page {match['page']} ---")
        for snippet in match["snippets"][:3]:  # Limit snippets per page
            lines.append(f"  ...{snippet}...")

    return "\n".join(lines)


def format_chapter_output(result: dict) -> str:
    """Format chapter content for CLI output."""
    lines = []
    lines.append(f"Book: {result['title']} (ID: {result['book_id']})")
    if result.get("chapter_title"):
        lines.append(f"Chapter: {result['chapter_title']}")
    lines.append(f"Pages: {result['start_page']}-{result['end_page']}")
    lines.append("=" * 60)

    for page_data in result.get("pages", []):
        lines.append(f"\n--- Page {page_data['page']} ---\n")
        for chunk in page_data.get("chunks", []):
            block_type = chunk.get("block_type", "")
            text = chunk.get("text", "")
            if block_type in ("SectionHeader",):
                lines.append(f"\n## {text}\n")
            elif block_type == "Equation":
                lines.append(f"\n[Equation]\n{text}\n")
            else:
                lines.append(text)

    return "\n".join(lines)


def format_references_output(result: dict) -> str:
    """Format reference analysis for CLI output."""
    lines = []
    lines.append(f"Book: {result['title']} (ID: {result['book_id']})")
    lines.append("=" * 60)
    lines.append("REFERENCE ANALYSIS")
    lines.append("")

    # Bibliography section
    if result.get("has_bibliography"):
        pages = result.get("bibliography_pages", [])
        lines.append(f"Bibliography/References section: YES (pages: {pages})")
    else:
        lines.append("Bibliography/References section: NO")

    lines.append("")

    # Citation patterns
    lines.append("Citation patterns found:")
    if result.get("citation_patterns"):
        for pat in result["citation_patterns"]:
            lines.append(f"  • {pat['pattern']}: {pat['count']} found")
            if pat.get("examples"):
                lines.append(f"    Examples: {', '.join(pat['examples'])}")
    else:
        lines.append("  (none detected)")

    lines.append("")

    # ISBN mentions
    lines.append("ISBN mentions:")
    if result.get("isbn_mentions"):
        for isbn in result["isbn_mentions"]:
            lines.append(f"  • {isbn}")
    else:
        lines.append("  (none found)")

    lines.append("")

    # Publisher mentions
    lines.append("Academic publisher mentions:")
    if result.get("publisher_mentions"):
        # Group by publisher
        by_pub = {}
        for pm in result["publisher_mentions"]:
            pub = pm["publisher"]
            if pub not in by_pub:
                by_pub[pub] = []
            by_pub[pub].append(pm["page"])
        for pub, pages in sorted(by_pub.items()):
            lines.append(f"  • {pub} (pages: {pages[:5]}{'...' if len(pages) > 5 else ''})")
    else:
        lines.append("  (none found)")

    lines.append("")
    lines.append("-" * 60)
    lines.append("Searches performed:")
    for s in result.get("searches_performed", []):
        lines.append(f"  • {s}")

    return "\n".join(lines)


def parse_args():
    """Parse command line arguments."""
    args = {
        "book_id": None,
        "page": None,
        "pages": None,  # "start-end" format
        "context": None,
        "list": False,
        "structure": False,
        "search": None,
        "chapter": None,
        "first": None,
        "references": False,
    }

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--book-id" and i + 1 < len(sys.argv):
            args["book_id"] = int(sys.argv[i + 1])
            i += 1
        elif arg == "--page" and i + 1 < len(sys.argv):
            args["page"] = int(sys.argv[i + 1])
            i += 1
        elif arg == "--pages" and i + 1 < len(sys.argv):
            args["pages"] = sys.argv[i + 1]
            i += 1
        elif arg == "--context" and i + 1 < len(sys.argv):
            args["context"] = int(sys.argv[i + 1])
            i += 1
        elif arg == "--search" and i + 1 < len(sys.argv):
            args["search"] = sys.argv[i + 1]
            i += 1
        elif arg == "--chapter" and i + 1 < len(sys.argv):
            args["chapter"] = sys.argv[i + 1]
            i += 1
        elif arg == "--first" and i + 1 < len(sys.argv):
            args["first"] = int(sys.argv[i + 1])
            i += 1
        elif arg == "--list":
            args["list"] = True
        elif arg == "--structure":
            args["structure"] = True
        elif arg == "--references":
            args["references"] = True
        elif arg in ("-h", "--help"):
            print("""Usage: librarian-read [OPTIONS]

Read content from indexed books for agent navigation.

Options:
  --book-id ID       Book ID (required except for --list)
  --page PAGE        Read a specific page
  --pages START-END  Read a page range (e.g., 105-110)
  --context N        Read N pages before and after --page (default: 0)
  --search TERM      Search for text within the book
  --chapter NAME|N   Read a chapter by name (fuzzy) or number
  --first N          Read the first N pages of content
  --references       Analyze citations and bibliography in the book
  --list             List all indexed books
  --structure        Show book structure (table of contents)
  -h, --help         Show this help

Examples:
  librarian-read --list
  librarian-read --book-id 156 --page 107
  librarian-read --book-id 156 --page 107 --context 2
  librarian-read --book-id 156 --pages 105-110
  librarian-read --book-id 156 --structure
  librarian-read --book-id 156 --search "Jacobian"
  librarian-read --book-id 156 --chapter "Preface"
  librarian-read --book-id 156 --chapter 3
  librarian-read --book-id 156 --first 5
  librarian-read --book-id 156 --references""")
            sys.exit(0)
        else:
            print(f"Unknown argument: {arg}", file=sys.stderr)
            sys.exit(1)
        i += 1

    return args


def main():
    """CLI entry point for librarian-read."""
    args = parse_args()
    config = load_config()

    # Handle --list
    if args["list"]:
        books = list_books(config)
        if not books:
            print("No indexed books found.")
            sys.exit(0)
        print(format_books_output(books))
        return

    # All other operations require book_id
    book_id = args["book_id"]
    if not book_id:
        print("Error: --book-id required (or use --list)", file=sys.stderr)
        sys.exit(1)

    # Handle --structure
    if args["structure"]:
        result = get_book_structure(book_id, config)
        if not result:
            print(f"Error: Book {book_id} not found or not indexed", file=sys.stderr)
            sys.exit(1)
        print(format_structure_output(result))
        return

    # Handle --references
    if args["references"]:
        result = find_references(book_id, config)
        if not result:
            print(f"Error: Book {book_id} not found or not indexed", file=sys.stderr)
            sys.exit(1)
        print(format_references_output(result))
        return

    # Handle --search
    if args["search"]:
        result = search_book(book_id, args["search"], config)
        if not result:
            print(f"No matches found for \"{args['search']}\" in book {book_id}",
                  file=sys.stderr)
            sys.exit(1)
        print(format_search_output(result))
        return

    # Handle --chapter
    if args["chapter"]:
        result = read_chapter(book_id, args["chapter"], config)
        if not result:
            print(f"Error: Chapter \"{args['chapter']}\" not found in book {book_id}",
                  file=sys.stderr)
            sys.exit(1)
        print(format_chapter_output(result))
        return

    # Handle --first
    if args["first"]:
        result = read_first_pages(book_id, args["first"], config)
        if not result:
            print(f"Error: Could not read first pages of book {book_id}", file=sys.stderr)
            sys.exit(1)
        print(format_pages_output(result))
        return

    # Handle page range (--pages)
    if args["pages"]:
        try:
            start, end = map(int, args["pages"].split("-"))
        except ValueError:
            print("Error: --pages format should be START-END (e.g., 105-110)", file=sys.stderr)
            sys.exit(1)
        result = read_pages(book_id, start, end, config)
        if not result:
            print(f"Error: No content found for pages {start}-{end} in book {book_id}",
                  file=sys.stderr)
            sys.exit(1)
        print(format_pages_output(result))
        return

    # Handle single page with optional context
    page = args["page"]
    if not page:
        print("Error: --page, --pages, --search, --chapter, --first, --references, or --structure required",
              file=sys.stderr)
        sys.exit(1)

    context = args["context"]
    if context:
        result = get_context(book_id, page, context, config)
        if not result:
            print(f"Error: No content found for page {page} (+/- {context}) in book {book_id}",
                  file=sys.stderr)
            sys.exit(1)
        print(format_pages_output(result))
    else:
        result = read_page(book_id, page, config)
        if not result:
            print(f"Error: Page {page} not found in book {book_id}", file=sys.stderr)
            sys.exit(1)
        print(format_page_output(result))


if __name__ == "__main__":
    main()
