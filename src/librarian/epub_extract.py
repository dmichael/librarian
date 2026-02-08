"""Native EPUB extraction — no marker, no GPU needed.

Parses EPUB structure directly via ebooklib, converts XHTML chapters to
markdown. Produces the same JSON block + markdown output format as marker
so the indexing pipeline works unchanged.

Usage:
    from librarian.epub_extract import extract_epub
    result = extract_epub(Path("book.epub"), book_id=33, output_dir=Path("converted/33"))
"""

import json
import re
from pathlib import Path

import ebooklib
from ebooklib import epub
from markdownify import markdownify
from bs4 import BeautifulSoup


def _classify_block(tag_name: str, text: str) -> str:
    """Map HTML tag to marker-compatible block_type."""
    if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return "SectionHeader"
    if tag_name in ("ul", "ol"):
        return "ListGroup"
    if tag_name == "table":
        return "Table"
    if tag_name == "figure":
        return "Figure"
    if tag_name == "blockquote":
        return "Text"
    return "Text"


def _extract_blocks_from_html(html: str, chapter_idx: int) -> list[dict]:
    """Parse an EPUB chapter's XHTML into marker-compatible blocks."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body") or soup

    blocks = []
    block_num = 0

    for element in body.children:
        if not hasattr(element, "name") or element.name is None:
            # NavigableString — skip whitespace, keep meaningful text
            text = element.strip()
            if text:
                blocks.append({
                    "id": f"/chapter/{chapter_idx}/Text/{block_num}",
                    "block_type": "Text",
                    "page": chapter_idx,
                    "html": f"<p>{text}</p>",
                })
                block_num += 1
            continue

        # Skip empty elements
        text = element.get_text(strip=True)
        if not text and element.name not in ("img", "figure", "table"):
            continue

        block_type = _classify_block(element.name, text)
        block_html = str(element)

        blocks.append({
            "id": f"/chapter/{chapter_idx}/{block_type}/{block_num}",
            "block_type": block_type,
            "page": chapter_idx,
            "html": block_html,
        })
        block_num += 1

    return blocks


def extract_epub(epub_path: Path, book_id: int, output_dir: Path) -> dict:
    """Extract an EPUB to JSON blocks + markdown.

    Args:
        epub_path: Path to the EPUB file
        book_id: Book ID for naming output files
        output_dir: Directory to write output files

    Returns:
        Dict with 'success', 'chunks_json', 'markdown', 'error',
        'block_count', 'chapter_count'
    """
    result = {
        "book_id": book_id,
        "success": False,
        "error": None,
        "block_count": 0,
        "chapter_count": 0,
    }

    try:
        book = epub.read_epub(str(epub_path), options={"ignore_ncx": True})
    except Exception as e:
        result["error"] = f"Failed to read EPUB: {e}"
        return result

    all_blocks = []
    md_parts = []
    chapter_idx = 0

    # Process spine items (reading order)
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        html = item.get_content().decode("utf-8", errors="replace")

        # Skip empty documents
        soup = BeautifulSoup(html, "html.parser")
        body = soup.find("body") or soup
        text = body.get_text(strip=True)
        if not text:
            continue

        chapter_idx += 1
        blocks = _extract_blocks_from_html(html, chapter_idx)
        all_blocks.extend(blocks)

        # Convert full chapter to markdown
        md = markdownify(html, heading_style="ATX").strip()
        if md:
            md_parts.append(md)

    if not all_blocks:
        result["error"] = "No content extracted from EPUB"
        return result

    # Write output files
    output_dir.mkdir(parents=True, exist_ok=True)

    chunks_data = {"blocks": all_blocks}
    chunks_json = json.dumps(chunks_data, ensure_ascii=False)
    (output_dir / f"{book_id}.json").write_text(chunks_json)

    markdown = "\n\n".join(md_parts)
    (output_dir / f"{book_id}.md").write_text(markdown)

    result["success"] = True
    result["block_count"] = len(all_blocks)
    result["chapter_count"] = chapter_idx

    return result
