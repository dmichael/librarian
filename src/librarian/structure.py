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
from dataclasses import dataclass, field


@dataclass
class Section:
    """A section within a chapter."""
    title: str
    page_start: int | None = None
    parent_chapter: int | None = None


@dataclass
class Chapter:
    """A chapter in the document."""
    number: int
    title: str
    page_start: int | None = None
    page_end: int | None = None
    sections: list[Section] = field(default_factory=list)
    summary: str = ""  # LLM-generated or extracted from content

    @property
    def breadcrumb(self) -> str:
        """Generate breadcrumb for this chapter."""
        return f"Chapter {self.number}: {self.title}"


@dataclass
class BookSection:
    """A major section of the book (e.g., 'Section One: An Investor's Guide')."""
    number: int | None
    title: str
    chapters: list[int] = field(default_factory=list)  # chapter numbers


@dataclass
class DocumentStructure:
    """Complete document structure with chapters and sections."""
    title: str
    book_sections: list[BookSection] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)

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

    # Patterns for structure detection
    # Match: # Chapter N, # Chapter N: Title, # Chapter N Title (no separator)
    chapter_num_pattern = re.compile(
        r'^#\s+\*{0,2}Chapter\s+(\d+)\*{0,2}(?:\s*[:\-–]?\s*(.*))?$',
        re.IGNORECASE
    )
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
        ch_match = chapter_num_pattern.match(line)
        if ch_match:
            # Save previous chapter's page end
            if current_chapter and last_page:
                current_chapter.page_end = last_page

            ch_num = int(ch_match.group(1))
            ch_title = ch_match.group(2) or ""

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
