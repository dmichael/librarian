"""Tests for sub-chapter section extraction from Marker blocks."""

from librarian.structure import (
    extract_structure_from_blocks,
    get_chapter_for_block,
    get_hierarchy_for_block,
    get_section_for_block,
)


def _block(block_type: str, text: str, page: int = 1) -> dict:
    return {"block_type": block_type, "html": f"<p>{text}</p>", "text": text, "page": page}


def _header(text: str, page: int = 1) -> dict:
    return _block("SectionHeader", text, page)


def test_sections_within_chapters():
    blocks = [
        _header("Chapter 1: Acoustics", page=1),
        _block("Text", "Intro to acoustics.", page=1),
        _header("Sound Waves", page=2),
        _block("Text", "Sound waves propagate...", page=2),
        _header("Resonance", page=3),
        _block("Text", "Resonance occurs when...", page=3),
        _header("Chapter 2: Optics", page=5),
        _block("Text", "Intro to optics.", page=5),
        _header("Refraction", page=6),
        _block("Text", "Snell's law...", page=6),
    ]

    structure = extract_structure_from_blocks(blocks, title="Physics")

    assert len(structure.chapters) == 2

    ch1 = structure.get_chapter(1)
    assert ch1 is not None
    section_titles = [s.title for s in ch1.sections]
    assert "Sound Waves" in section_titles
    assert "Resonance" in section_titles

    ch2 = structure.get_chapter(2)
    assert ch2 is not None
    assert [s.title for s in ch2.sections] == ["Refraction"]


def test_section_block_mapping():
    blocks = [
        _header("Chapter 1: Intro", page=1),
        _block("Text", "Opening.", page=1),
        _header("Background", page=2),
        _block("Text", "Historical context.", page=2),
        _block("Text", "More context.", page=2),
        _header("Motivation", page=3),
        _block("Text", "Why this matters.", page=3),
    ]

    structure = extract_structure_from_blocks(blocks, title="Test")

    assert get_section_for_block(structure, 0) is None  # chapter header itself
    assert get_section_for_block(structure, 1) is None  # before first section
    assert get_section_for_block(structure, 2) == "Background"
    assert get_section_for_block(structure, 3) == "Background"
    assert get_section_for_block(structure, 4) == "Background"
    assert get_section_for_block(structure, 5) == "Motivation"
    assert get_section_for_block(structure, 6) == "Motivation"


def test_section_resets_at_chapter_boundary():
    blocks = [
        _header("Chapter 1: First", page=1),
        _header("Section A", page=2),
        _block("Text", "Content A.", page=2),
        _header("Chapter 2: Second", page=5),
        _block("Text", "Content B.", page=5),
    ]

    structure = extract_structure_from_blocks(blocks, title="Test")

    assert get_section_for_block(structure, 2) == "Section A"
    assert get_section_for_block(structure, 4) is None


def test_article_flat_sections():
    """Articles have sections but no chapters."""
    blocks = [
        _header("Introduction", page=1),
        _block("Text", "We studied frogs.", page=1),
        _header("Methods", page=2),
        _block("Text", "Recordings were made.", page=2),
        _header("Results", page=3),
        _block("Text", "Call rates varied.", page=3),
        _header("Discussion", page=4),
        _block("Text", "Our findings confirm...", page=4),
    ]

    structure = extract_structure_from_blocks(blocks, title="Frog Paper")

    assert len(structure.chapters) == 0

    assert get_section_for_block(structure, 1) == "Introduction"
    assert get_section_for_block(structure, 3) == "Methods"
    assert get_section_for_block(structure, 5) == "Results"
    assert get_section_for_block(structure, 7) == "Discussion"


def test_skip_titles_excluded():
    blocks = [
        _header("Chapter 1: Main", page=1),
        _block("Text", "Content.", page=1),
        _header("Good Section", page=2),
        _block("Text", "More content.", page=2),
        _header("Chapter Summary", page=3),
        _block("Text", "Summary text.", page=3),
        _header("References", page=4),
        _block("Text", "Biblio.", page=4),
    ]

    structure = extract_structure_from_blocks(blocks, title="Test")

    ch1 = structure.get_chapter(1)
    assert ch1 is not None
    section_titles = [s.title for s in ch1.sections]
    assert "Good Section" in section_titles
    assert "Chapter Summary" not in section_titles
    assert "References" not in section_titles


def test_bold_markers_stripped():
    blocks = [
        _header("Chapter 1: Test", page=1),
        {"block_type": "SectionHeader", "html": "<h2>**Bold Section**</h2>", "text": "**Bold Section**", "page": 2},
        _block("Text", "Content.", page=2),
    ]

    structure = extract_structure_from_blocks(blocks, title="Test")
    ch1 = structure.get_chapter(1)
    assert ch1 is not None
    assert ch1.sections[0].title == "Bold Section"


def test_chapter_and_section_together():
    blocks = [
        _header("Chapter 3: Synchrony", page=10),
        _block("Text", "Overview.", page=10),
        _header("Coupled Oscillators", page=11),
        _block("Text", "Two oscillators...", page=11),
        _block("Equation", "$$\\omega = 2\\pi f$$", page=11),
        _block("Text", "where f is frequency.", page=11),
        _header("Phase Response", page=12),
        _block("Text", "The PRC shows...", page=12),
    ]

    structure = extract_structure_from_blocks(blocks, title="Sync Book")

    ch = get_chapter_for_block(structure, 4)
    assert ch is not None
    assert ch.number == 3

    sec = get_section_for_block(structure, 4)
    assert sec == "Coupled Oscillators"

    sec2 = get_section_for_block(structure, 7)
    assert sec2 == "Phase Response"


def test_hierarchy_for_block_chapter_and_section():
    blocks = [
        _header("Chapter 3: Synchrony", page=10),
        _block("Text", "Overview.", page=10),
        _header("Coupled Oscillators", page=11),
        _block("Text", "Two oscillators...", page=11),
    ]
    structure = extract_structure_from_blocks(blocks, title="Sync Book")

    ctx = get_hierarchy_for_block(structure, 3)
    assert ctx["chapter_num"] == 3
    assert ctx["section_title"] == "Coupled Oscillators"
    assert ctx["breadcrumb"].endswith("Coupled Oscillators")
    assert "Coupled Oscillators" in ctx["breadcrumb"]


def test_hierarchy_for_block_flat_article_keeps_section():
    """Regression: chapterless articles must still get section + breadcrumb.

    Previously index_book gated the section lookup behind `if chapter:`, so
    papers (Methods/Results/Discussion, no chapters) lost all section metadata.
    """
    blocks = [
        _header("Introduction", page=1),
        _block("Text", "We studied frogs.", page=1),
        _header("Methods", page=2),
        _block("Text", "Recordings were made.", page=2),
    ]
    structure = extract_structure_from_blocks(blocks, title="Frog Paper")
    assert len(structure.chapters) == 0  # no chapters — the bug condition

    ctx = get_hierarchy_for_block(structure, 3)  # a Text block under "Methods"
    assert ctx["chapter_num"] is None
    assert ctx["chapter_title"] == ""
    assert ctx["section_title"] == "Methods"
    assert ctx["breadcrumb"] == "Methods"


def test_hierarchy_for_block_none_when_unmapped():
    blocks = [
        _header("Chapter 1: Intro", page=1),
        _block("Text", "Body.", page=1),
    ]
    structure = extract_structure_from_blocks(blocks, title="Book")

    # block_idx None and out-of-range both yield empty context
    for idx in (None, 999):
        ctx = get_hierarchy_for_block(structure, idx)
        assert ctx["chapter_num"] is None
        assert ctx["section_title"] == ""
        assert ctx["breadcrumb"] == ""
