"""Extract document structure from markdown files.

Parses markdown headers to build a hierarchical document skeleton:
- Book sections (e.g., "Section One: An Investor's Guide")
- Chapters with number, title, page range
- Sections within chapters

This structure enables:
- Hierarchical metadata on chunks (chapter_num, section_title, breadcrumb)
- Chapter-level retrieval for broad queries
- Navigation queries like "what's in chapter 5?"
"""

import re

from pydantic import BaseModel, ConfigDict, Field, computed_field

from librarian.htmltext import html_to_text

# Chapter heading grammar — the single owner of "what counts as a chapter
# head", shared by the markdown path (parse_structure) and the block path
# (extract_structure_from_blocks). Each pattern yields (number, title).
CHAPTER_HEADING_PATTERNS = [
    # "Chapter 5: Title" or "Chapter 5"
    re.compile(r'^Chapter\s+(\d+)\s*[:\-–]?\s*(.*)$', re.IGNORECASE),
    # "Rule No. 3: Title" or "Rule No. 3"
    re.compile(r'^Rule\s+No\.?\s*(\d+)\s*[:\-–]?\s*(.*)$', re.IGNORECASE),
    # "Part 2: Title"
    re.compile(r'^Part\s+(\d+)\s*[:\-–]?\s*(.*)$', re.IGNORECASE),
    # "Cycle 5: Title" (e.g., Buzsáki)
    re.compile(r'^Cycle\s+(\d+)\s*[:\-–]?\s*(.*)$', re.IGNORECASE),
    # "Lesson 3: Title"
    re.compile(r'^Lesson\s+(\d+)\s*[:\-–]?\s*(.*)$', re.IGNORECASE),
    # "1. TITLE" or "3. Title" (numbered with dot, common in many books)
    re.compile(r'^(\d{1,2})\.\s+(.+)$'),
]


def match_chapter_heading(text: str) -> tuple[int, str] | None:
    """Match text against the chapter grammar, returning (number, title) or None."""
    cleaned = re.sub(r'\*{1,2}', '', text).strip()
    for pattern in CHAPTER_HEADING_PATTERNS:
        m = pattern.match(cleaned)
        if m:
            return int(m.group(1)), (m.group(2).strip() if m.group(2) else "")
    return None


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    page_start: int | None = None
    parent_chapter: int | None = None


class Chapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    title: str
    page_start: int | None = None
    page_end: int | None = None
    sections: list[Section] = Field(default_factory=list)
    summary: str = ""

    @computed_field
    @property
    def breadcrumb(self) -> str:
        return f"Chapter {self.number}: {self.title}"


class BookSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int | None = None
    title: str
    chapters: list[int] = Field(default_factory=list)


class DocumentStructure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    book_sections: list[BookSection] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    block_to_chapter: dict[int, int] = Field(default_factory=dict, exclude=True)
    block_to_section: dict[int, str] = Field(default_factory=dict, exclude=True)

    def get_chapter(self, num: int) -> Chapter | None:
        """Get chapter by number."""
        for ch in self.chapters:
            if ch.number == num:
                return ch
        return None

    def get_chapter_at_page(self, page: int) -> Chapter | None:
        """Find chapter containing a given page."""
        for i, ch in enumerate(self.chapters):
            if not ch.page_start:
                continue

            # Check if page is at or after chapter start
            if page >= ch.page_start:
                # Check if there's a next chapter with a known start
                is_last = (i == len(self.chapters) - 1)
                if not is_last:
                    next_ch = self.chapters[i + 1]
                    if next_ch.page_start and page >= next_ch.page_start:
                        continue  # Page is in a later chapter

                # Page is in this chapter
                return ch

        return None

    def get_section_at_page(self, page: int, chapter: Chapter) -> Section | None:
        """Find section within chapter containing a given page."""
        if not chapter.sections:
            return None
        for i, sec in enumerate(chapter.sections):
            if sec.page_start and page >= sec.page_start:
                # Check if before next section
                if i + 1 < len(chapter.sections):
                    next_sec = chapter.sections[i + 1]
                    if next_sec.page_start and page < next_sec.page_start:
                        return sec
                else:
                    return sec  # Last section in chapter
        return None


def extract_page_from_text(text: str) -> int | None:
    """Extract first page number from text using Marker's embedded markers."""
    # Look for <span id="page-XXX"> or image refs like _page_XXX_
    page_spans = re.findall(r'page-(\d+)', text)
    page_images = re.findall(r'_page_(\d+)_', text)
    pages = page_spans + page_images
    if pages:
        return int(pages[0])
    return None


def parse_structure(content: str, title: str = "") -> DocumentStructure:
    """Parse markdown content to extract document structure.

    Handles several header patterns found in converted PDFs:
    - `# Chapter N` or `# Chapter N: Title` - chapter headers
    - `# **Title**` following chapter number - chapter titles
    - `### **Section Title**` - sections within chapters
    - `### Section One: ...` - book sections (groups of chapters)
    - `### **Chapter Summary**` - end of chapter marker

    Args:
        content: Full markdown content
        title: Book title (from metadata)

    Returns:
        DocumentStructure with chapters and sections
    """
    structure = DocumentStructure(title=title)
    lines = content.split('\n')

    current_chapter: Chapter | None = None
    current_book_section: BookSection | None = None
    last_page: int | None = None

    # Chapter heads must be level-1 headers in the markdown path; the
    # heading grammar itself is shared with the block path.
    chapter_head_prefix = re.compile(r'^#\s+(.+)$')
    # Title can be level 1/2/3 header, with or without bold
    # Match: # **Title**, ### **Title**, # Title (non-bold)
    chapter_title_pattern = re.compile(r'^#{1,3}\s+\*{0,2}([^#\n]+?)\*{0,2}\s*$')
    section_pattern = re.compile(r'^#{2,3}\s+\*{0,2}(.+?)\*{0,2}\s*$')
    book_section_pattern = re.compile(
        r'^#{2,3}\s+\*{0,2}Section\s+(One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|\d+)[:\s]+(.+?)\*{0,2}\s*$',
        re.IGNORECASE
    )
    chapter_summary_pattern = re.compile(r'^#{2,3}\s+\*{0,2}Chapter\s+Summary\*{0,2}\s*$', re.IGNORECASE)

    # Map word numbers to integers
    word_to_num = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10
    }

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track page numbers
        page = extract_page_from_text(line)
        if page:
            last_page = page

        # Check for book section (e.g., "### Section One: An Investor's Guide")
        book_sec_match = book_section_pattern.match(line)
        if book_sec_match:
            sec_num_str = book_sec_match.group(1).lower()
            sec_num = word_to_num.get(sec_num_str) or int(sec_num_str) if sec_num_str.isdigit() else None
            sec_title = book_sec_match.group(2).strip()

            current_book_section = BookSection(number=sec_num, title=sec_title)
            structure.book_sections.append(current_book_section)
            i += 1
            continue

        # Check for chapter header
        head_match = chapter_head_prefix.match(line)
        ch_result = match_chapter_heading(head_match.group(1)) if head_match else None
        if ch_result:
            # Save previous chapter's page end
            if current_chapter and last_page:
                current_chapter.page_end = last_page

            ch_num, ch_title = ch_result

            # Look for title on next few lines if not inline (skip empty lines)
            if not ch_title:
                for j in range(i + 1, min(i + 4, len(lines))):
                    next_line = lines[j].strip()
                    if not next_line:
                        continue  # Skip empty lines
                    title_match = chapter_title_pattern.match(lines[j])
                    if title_match:
                        potential_title = title_match.group(1).strip()
                        # Skip lines that look like section intros, not titles
                        skip_phrases = ['this chapter', 'in this chapter', 'chapter reviews']
                        if not any(phrase in potential_title.lower() for phrase in skip_phrases):
                            ch_title = potential_title
                    break  # Stop after first non-empty line

            # Look ahead for first page marker if we don't have one yet
            chapter_page_start = last_page
            if chapter_page_start is None:
                for j in range(i + 1, min(i + 20, len(lines))):
                    ahead_page = extract_page_from_text(lines[j])
                    if ahead_page:
                        chapter_page_start = ahead_page
                        break

            current_chapter = Chapter(
                number=ch_num,
                title=ch_title.strip(),
                page_start=chapter_page_start
            )
            structure.chapters.append(current_chapter)

            # Track chapter in current book section
            if current_book_section:
                current_book_section.chapters.append(ch_num)

            i += 1
            continue

        # Check for chapter summary (end marker)
        if chapter_summary_pattern.match(line):
            if current_chapter and last_page:
                current_chapter.page_end = last_page
            i += 1
            continue

        # Check for section within chapter (skip if it's a book section)
        sec_match = section_pattern.match(line)
        if sec_match and current_chapter and not book_section_pattern.match(line):
            sec_title = sec_match.group(1).strip()
            # Skip non-content headers
            if sec_title.lower() not in ('chapter summary', 'notes', 'references'):
                section = Section(
                    title=sec_title,
                    page_start=last_page,
                    parent_chapter=current_chapter.number
                )
                current_chapter.sections.append(section)

        i += 1

    # Finalize last chapter
    if current_chapter and last_page:
        current_chapter.page_end = last_page

    return structure


def get_context_for_page(
    structure: DocumentStructure,
    page: int | None
) -> dict:
    """Get hierarchical context for a given page number.

    Returns metadata dict with:
    - chapter_num: int or None
    - chapter_title: str or ""
    - section_title: str or ""
    - breadcrumb: str like "Chapter 5 > Distribution Expenses"
    """
    if page is None:
        return {
            "chapter_num": None,
            "chapter_title": "",
            "section_title": "",
            "breadcrumb": "",
        }

    chapter = structure.get_chapter_at_page(page)
    if not chapter:
        return {
            "chapter_num": None,
            "chapter_title": "",
            "section_title": "",
            "breadcrumb": "",
        }

    section = structure.get_section_at_page(page, chapter)
    section_title = section.title if section else ""

    breadcrumb = chapter.breadcrumb
    if section_title:
        breadcrumb = f"{breadcrumb} > {section_title}"

    return {
        "chapter_num": chapter.number,
        "chapter_title": chapter.title,
        "section_title": section_title,
        "breadcrumb": breadcrumb,
    }


def get_chapter_toc(structure: DocumentStructure) -> str:
    """Generate a table of contents from document structure.

    Useful for LLM context when answering "what's in this book" queries.
    """
    lines = []
    if structure.title:
        lines.append(f"# {structure.title}")
        lines.append("")

    for chapter in structure.chapters:
        lines.append(f"## Chapter {chapter.number}: {chapter.title}")
        for section in chapter.sections:
            lines.append(f"  - {section.title}")

    return "\n".join(lines)


def _strip_html(html: str) -> str:
    """Strip HTML tags from text."""
    return html_to_text(html, "strip")


def extract_structure_from_blocks(blocks: list[dict], title: str = "") -> DocumentStructure:
    """Extract document structure from marker JSON blocks.

    This is the PRIMARY method for structure extraction. Uses BLOCK ORDER
    (which reflects reading order) rather than page numbers (which may be
    scrambled due to PDF extraction issues).

    The algorithm:
    1. First pass: collect chapter titles from TOC (multiple chapters on same page)
    2. Second pass: find actual chapter content starts (non-TOC chapter headers)
    3. Assign block ranges to chapters based on reading order

    Args:
        blocks: List of block dicts from marker JSON output.
                Each block has: html, page, block_type, id
        title: Book title from metadata

    Returns:
        DocumentStructure with chapters in correct reading order, plus
        block_to_chapter mapping for assigning content to chapters.
    """
    structure = DocumentStructure(title=title)

    # First pass: collect all chapter occurrences and detect TOC
    chapter_occurrences: list[tuple[int, int, str, int]] = []  # (block_idx, ch_num, title, page)
    for idx, block in enumerate(blocks):
        if block.get('block_type') != 'SectionHeader':
            continue
        # Handle both raw HTML blocks and processed text blocks
        text = block.get('text') or _strip_html(block.get('html', ''))
        # Strip markdown heading markers
        text = re.sub(r'^#+\s*', '', text).strip()
        result = match_chapter_heading(text)
        if result:
            ch_num, ch_title = result
            page = block.get('page') or 0
            chapter_occurrences.append((idx, ch_num, ch_title, page))

    # Detect TOC: early cluster of chapters (many chapters within small block range)
    # Heuristic: if we see multiple different chapter numbers within 50 blocks, it's TOC
    toc_end_idx = 0
    if len(chapter_occurrences) >= 4:
        first_idx = chapter_occurrences[0][0]
        for i, (idx, ch_num, title, page) in enumerate(chapter_occurrences[:10]):
            if idx - first_idx < 100 and i >= 3:  # Multiple chapters within 100 blocks
                toc_end_idx = idx + 1  # Mark everything up to here as TOC

    # Collect chapter info: prefer later occurrences (content) over earlier (TOC)
    chapter_data: dict[int, tuple[int, str, int]] = {}  # ch_num -> (block_idx, title, page)
    for idx, ch_num, ch_title, page in chapter_occurrences:
        if idx < toc_end_idx:
            # TOC entry - only take title if we don't have one
            if ch_num not in chapter_data and ch_title:
                chapter_data[ch_num] = (None, ch_title, None)  # No block/page yet
            elif ch_num in chapter_data and not chapter_data[ch_num][1] and ch_title:
                _, _, existing_page = chapter_data[ch_num]
                chapter_data[ch_num] = (None, ch_title, existing_page)
        else:
            # Content entry - use this block index and page
            existing = chapter_data.get(ch_num)
            if existing:
                _, existing_title, _ = existing
                ch_title = ch_title or existing_title
            chapter_data[ch_num] = (idx, ch_title, page)

    # Build chapters sorted by number
    for ch_num in sorted(chapter_data.keys()):
        block_idx, ch_title, page = chapter_data[ch_num]
        chapter = Chapter(
            number=ch_num,
            title=ch_title or "",
            page_start=page,
        )
        structure.chapters.append(chapter)

    # Find content chapter start blocks (non-TOC chapter headers)
    content_chapter_starts = []  # [(block_idx, ch_num)]
    chapter_block_set: set[int] = set()
    for idx, ch_num, _, _ in chapter_occurrences:
        if idx >= toc_end_idx:
            content_chapter_starts.append((idx, ch_num))
            chapter_block_set.add(idx)
    content_chapter_starts.sort(key=lambda x: x[0])

    # Build block-to-chapter mapping
    if content_chapter_starts:
        current_chapter_num = None
        chapter_idx = 0
        for block_idx in range(len(blocks)):
            while (chapter_idx < len(content_chapter_starts) and
                   block_idx >= content_chapter_starts[chapter_idx][0]):
                current_chapter_num = content_chapter_starts[chapter_idx][1]
                chapter_idx += 1
            if current_chapter_num is not None:
                structure.block_to_chapter[block_idx] = current_chapter_num

    # Second pass: collect sections from SectionHeader blocks that weren't
    # matched as chapters and are past the TOC.  Works for both:
    #   - books (sections nested under chapters)
    #   - articles (flat sections like METHODS, RESULTS with no chapters)
    skip_titles = {"chapter summary", "notes", "references", "bibliography"}
    has_chapters = bool(structure.chapters)

    section_starts: list[tuple[int, str]] = []  # (block_idx, title)
    for idx, block in enumerate(blocks):
        if block.get("block_type") != "SectionHeader":
            continue
        if idx < toc_end_idx or idx in chapter_block_set:
            continue

        text = block.get("text") or _strip_html(block.get("html", ""))
        text = re.sub(r"^#+\s*", "", text).strip()
        text = re.sub(r"\*{1,2}", "", text).strip()
        if not text or text.lower() in skip_titles:
            continue

        if has_chapters:
            ch_num = structure.block_to_chapter.get(idx)
            if ch_num is None:
                continue
            chapter = structure.get_chapter(ch_num)
            if chapter is None:
                continue
            page = block.get("page")
            chapter.sections.append(Section(
                title=text,
                page_start=page if isinstance(page, int) else None,
                parent_chapter=ch_num,
            ))

        section_starts.append((idx, text))

    # Build block-to-section mapping: each block gets the most recent
    # section title. For books, reset at chapter boundaries.
    current_section: str | None = None
    section_idx = 0
    current_ch: int | None = None
    for block_idx in range(len(blocks)):
        if has_chapters:
            ch_num = structure.block_to_chapter.get(block_idx)
            if ch_num is not None and ch_num != current_ch:
                current_ch = ch_num
                current_section = None
        while (section_idx < len(section_starts)
               and block_idx >= section_starts[section_idx][0]):
            current_section = section_starts[section_idx][1]
            section_idx += 1
        if current_section:
            structure.block_to_section[block_idx] = current_section

    return structure


def get_section_for_block(structure: DocumentStructure, block_idx: int) -> str | None:
    """Get the section title for a block, based on reading order."""
    return structure.block_to_section.get(block_idx)


# Exact-match titles (whole heading must be one of these) that mark the start of
# back matter. Exact-match avoids catching body headings like "Index Funds".
_BACK_MATTER_TITLE = re.compile(
    r"^\s*(index|about the authors?|colophon"
    r"|(wiley\s+)?end[- ]user license agreement)\s*$",
    re.IGNORECASE,
)


def _body_end_index(blocks: list[dict]) -> int | None:
    """First block where back matter begins, searched in the document's back half.

    Books close the body with conventional back matter: an "Index", an
    "About the Author(s)" / "Colophon" / EULA heading, or the index's run of
    single-letter headings (A, B, C, ...). Returns the earliest such block index
    in the back half of the document, or None if none is found. Searching only
    the back half avoids front-matter false positives.
    """
    n = len(blocks)
    if n < 20:
        return None
    run_len = 0
    run_start: int | None = None
    for idx in range(n // 2, n):
        block = blocks[idx]
        if block.get("block_type") != "SectionHeader":
            continue
        text = block.get("text") or _strip_html(block.get("html", ""))
        text = re.sub(r"^#+\s*", "", text).strip()
        text = re.sub(r"\*{1,2}", "", text).strip()
        if not text:
            continue
        if _BACK_MATTER_TITLE.match(text):
            return idx
        if len(text) <= 2:  # an index letter (A, B, ...), possibly OCR-garbled
            if run_len == 0:
                run_start = idx
            run_len += 1
            if run_len >= 4:
                return run_start
        else:
            run_len = 0
            run_start = None
    return None


_AUTO_BODY_END = -1  # sentinel: detect the boundary via the marker heuristic


def trim_back_matter(
    structure: DocumentStructure, blocks: list[dict], body_end: int | None = _AUTO_BODY_END
) -> DocumentStructure:
    """Drop back matter so it doesn't fold into the last chapter.

    `body_end` is the block where back matter begins: pass the model-identified
    boundary from the chapter audit (an int, or None meaning "no back matter"),
    or leave it at the default to fall back to the _body_end_index marker
    heuristic (used on the no-LLM path). Everything at or after the boundary is
    removed from the chapter/section assignment, so the final chapter ends with
    the body; the back-matter text is still embedded and searchable, just not
    mis-filed as sections of the last chapter.
    """
    # Use the model's boundary when it found one; otherwise (default, or the
    # model returned None — it can miss an obvious "Index" when it runs peekless
    # on a flat-heading book) fall back to the marker heuristic as a backstop.
    if body_end is None or body_end == _AUTO_BODY_END:
        body_end = _body_end_index(blocks)
    if body_end is None:
        return structure
    end = body_end
    # Earliest block per section title from the current mapping, so we can also
    # drop heuristic chapter.sections that sit in the back matter — trimming the
    # block_to_* dicts alone leaves the Section objects (which survive when a
    # chapter's section audit later fails and keeps its heuristic sections).
    title_block: dict[str, int] = {}
    for i, t in structure.block_to_section.items():
        if i < title_block.get(t, len(blocks)):
            title_block[t] = i
    structure.block_to_chapter = {
        i: c for i, c in structure.block_to_chapter.items() if i < end
    }
    structure.block_to_section = {
        i: t for i, t in structure.block_to_section.items() if i < end
    }
    kept = set(structure.block_to_chapter.values())
    structure.chapters = [c for c in structure.chapters if c.number in kept]
    for chapter in structure.chapters:
        chapter.sections = [
            s for s in chapter.sections if title_block.get(s.title, -1) < end
        ]
    return structure


def get_chapter_for_block(structure: DocumentStructure, block_idx: int) -> Chapter | None:
    """Get the chapter that a block belongs to, based on reading order.

    Uses the block-to-chapter mapping created during structure extraction.
    This is more reliable than page-based lookup when page numbers are scrambled.
    """
    ch_num = structure.block_to_chapter.get(block_idx)
    if ch_num is None:
        return None

    return structure.get_chapter(ch_num)


def get_hierarchy_for_block(structure: DocumentStructure, block_idx: int | None) -> dict:
    """Hierarchical context for a block, by reading order.

    Returns the same dict shape as get_context_for_page
    (chapter_num, chapter_title, section_title, breadcrumb). Works for
    chapterless documents (flat article sections like Methods/Results) as well
    as chaptered books — a section is attached even when no chapter is present.
    """
    chapter = get_chapter_for_block(structure, block_idx) if block_idx is not None else None
    section = get_section_for_block(structure, block_idx) if block_idx is not None else None

    crumbs = []
    if chapter:
        crumbs.append(chapter.breadcrumb)
    if section:
        crumbs.append(section)

    return {
        "chapter_num": chapter.number if chapter else None,
        "chapter_title": chapter.title if chapter else "",
        "section_title": section or "",
        "breadcrumb": " > ".join(crumbs),
    }


def validate_structure(structure: DocumentStructure, total_pages: int | None = None) -> dict:
    """Validate extracted structure and return diagnostic info.

    Returns:
        {
            "chapter_count": int,
            "chapters_with_pages": int,
            "page_coverage": float (0-1),
            "warnings": list[str],
        }
    """
    warnings = []

    chapter_count = len(structure.chapters)
    chapters_with_pages = sum(1 for ch in structure.chapters if ch.page_start)

    if chapter_count == 0:
        warnings.append("No chapters detected")
    elif chapters_with_pages < chapter_count:
        warnings.append(f"Only {chapters_with_pages}/{chapter_count} chapters have page numbers")

    # Check for page order issues
    last_page = 0
    for ch in structure.chapters:
        if ch.page_start and ch.page_start < last_page:
            warnings.append(f"Chapter {ch.number} page {ch.page_start} is before previous chapter")
        if ch.page_start:
            last_page = ch.page_start

    # Estimate page coverage
    page_coverage = 0.0
    if total_pages and structure.chapters:
        covered_pages = sum(
            (ch.page_end or total_pages) - (ch.page_start or 0)
            for ch in structure.chapters
            if ch.page_start
        )
        page_coverage = min(1.0, covered_pages / total_pages)

    return {
        "chapter_count": chapter_count,
        "chapters_with_pages": chapters_with_pages,
        "page_coverage": page_coverage,
        "warnings": warnings,
    }
