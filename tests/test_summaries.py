"""Tests for the summary hierarchy (book / chapter / section)."""

import pytest

pytest.importorskip("llama_index")

import librarian.index as index_mod
import librarian.llm as llm
from librarian.metadata_types import META_LEVEL, META_SECTION_TITLE
from librarian.structure import extract_structure_from_blocks, parse_structure


@pytest.fixture
def stub_llm(monkeypatch):
    calls = []

    def fake_complete(prompt, config, max_tokens=1024, timeout=120.0):
        calls.append(prompt)
        return f"Summary #{len(calls)}."

    monkeypatch.setattr(llm, "complete", fake_complete)
    return calls


METADATA = {"id": 7, "title": "Cycles Book", "authors": [], "subjects": [], "tags": [],
            "*library": "money-reading-list", "source_path": "x.pdf"}


def _header(idx, text, page=1):
    return {"block_type": "SectionHeader", "text": text, "html": "", "page": page}


def _text_block(text, page=1):
    return {"block_type": "Text", "text": text, "html": "", "page": page}


def test_section_only_book_gets_book_and_section_summaries(stub_llm):
    blocks = [
        _header(0, "Trading the Cycles"),
        _text_block("cycle analysis " * 200),   # ~3000 chars — substantial
        _header(2, "Tiny Note"),
        _text_block("too short"),               # below the threshold
    ]
    structure = extract_structure_from_blocks(blocks, title="Cycles Book")
    assert not structure.chapters  # precondition: section-only book

    nodes = index_mod.build_summary_nodes(
        structure, "full markdown content " * 50, METADATA, {}, blocks=blocks,
    )

    levels = [n.metadata[META_LEVEL] for n in nodes]
    assert levels.count("book") == 1
    assert levels.count("section") == 1
    section_node = next(n for n in nodes if n.metadata[META_LEVEL] == "section")
    assert section_node.metadata[META_SECTION_TITLE] == "Trading the Cycles"
    assert section_node.metadata["chapter_num"] is None
    # book summary + 1 substantial section = 2 LLM calls (tiny section skipped)
    assert len(stub_llm) == 2


def test_chaptered_book_gets_book_and_chapter_summaries(stub_llm):
    content = (
        "# Chapter 1: Oscillators\n\n" + ("oscillator basics " * 100) + "\n\n"
        "# Chapter 2: Timing Bands\n\n" + ("timing band detail " * 100) + "\n"
    )
    structure = parse_structure(content, title="Cycles Book")
    assert len(structure.chapters) == 2

    nodes = index_mod.build_summary_nodes(structure, content, METADATA, {})

    levels = [n.metadata[META_LEVEL] for n in nodes]
    assert levels.count("book") == 1
    assert levels.count("chapter") == 2
    chapter_nums = {n.metadata["chapter_num"] for n in nodes if n.metadata[META_LEVEL] == "chapter"}
    assert chapter_nums == {1, 2}


def test_section_cap_is_enforced_and_logged(stub_llm, capsys):
    blocks = []
    for i in range(50):
        blocks.append(_header(i * 2, f"Section {i:02d}"))
        blocks.append(_text_block(f"content {i} " * 200))
    structure = extract_structure_from_blocks(blocks, title="Cycles Book")

    nodes = index_mod.build_summary_nodes(structure, "content", METADATA, {}, blocks=blocks)

    section_nodes = [n for n in nodes if n.metadata[META_LEVEL] == "section"]
    assert len(section_nodes) == index_mod.MAX_SECTION_SUMMARIES
    assert "capping section summaries" in capsys.readouterr().err


def test_llm_failure_still_yields_no_phantom_nodes(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "")

    blocks = [_header(0, "Only Section"), _text_block("x " * 1000)]
    structure = extract_structure_from_blocks(blocks, title="Cycles Book")
    nodes = index_mod.build_summary_nodes(structure, "content", METADATA, {}, blocks=blocks)

    # No summaries could be generated -> no nodes (and no crash)
    assert nodes == []
