"""Tests for the LLM structure audit pass."""

import librarian.structure_audit as audit_mod
from librarian.structure import extract_structure_from_blocks


def _header(text: str, page: int = 1) -> dict:
    return {"block_type": "SectionHeader", "text": text, "html": "", "page": page}


def _text(text: str, page: int = 1) -> dict:
    return {"block_type": "Text", "text": text, "html": "", "page": page}


def test_audit_repairs_spelled_out_chapters(monkeypatch):
    blocks = [
        _header("TABLE OF CONTENTS", page=1),
        _header("Chapter One - FOCUS ON CYCLES", page=1),
        _header("Chapter Two - TECHNICAL TOOLS WITH CYCLES", page=1),
        _header("Chapter One FOCUS ON CYCLES", page=5),
        _text("Cycle text.", page=5),
        _header("The Nature of Cycles", page=6),
        _text("More cycle text.", page=6),
        _header("Chapter Two", page=20),
        _header("TECHNICAL TOOLS WITH CYCLES", page=20),
        _text("Tool text.", page=20),
    ]
    structure = extract_structure_from_blocks(blocks, title="Cycles")
    assert structure.chapters == []

    def fake_complete(prompt, config, max_tokens=1024, timeout=120.0):
        assert "Chapter One - FOCUS ON CYCLES" in prompt
        return """
        {
          "action": "replace_structure",
          "chapters": [
            {"number": 1, "title": "Focus on Cycles", "start_block_idx": 3,
             "evidence": "Chapter One FOCUS ON CYCLES"},
            {"number": 2, "title": "Technical Tools with Cycles", "start_block_idx": 7,
             "evidence": "Chapter Two"}
          ]
        }
        """

    monkeypatch.setattr(audit_mod.llm, "complete", fake_complete)

    result = audit_mod.audit_structure_with_llm(structure, blocks, "Cycles", {})

    assert result.applied
    assert [ch.number for ch in result.structure.chapters] == [1, 2]
    assert result.structure.get_chapter(1).title == "Focus on Cycles"
    assert result.structure.block_to_chapter[6] == 1
    assert result.structure.block_to_chapter[9] == 2
    assert result.structure.get_chapter(1).sections[0].title == "The Nature of Cycles"


def test_audit_no_change_keeps_existing_structure(monkeypatch):
    blocks = [_header("Chapter 1: Intro"), _text("Intro text")]
    structure = extract_structure_from_blocks(blocks, title="Book")
    assert len(structure.chapters) == 1

    monkeypatch.setattr(
        audit_mod.llm,
        "complete",
        lambda *args, **kwargs: '{"action":"no_change","chapters":[]}',
    )

    result = audit_mod.audit_structure_with_llm(structure, blocks, "Book", {})

    assert not result.applied
    assert result.structure is structure
    assert len(result.structure.chapters) == 1


def test_audit_rejects_duplicate_or_unordered_chapters(monkeypatch):
    blocks = [
        _header("Chapter One"),
        _text("One"),
        _header("Chapter Two"),
        _text("Two"),
    ]
    structure = extract_structure_from_blocks(blocks, title="Book")

    monkeypatch.setattr(
        audit_mod.llm,
        "complete",
        lambda *args, **kwargs: """
        {"action":"replace_structure","chapters":[
          {"number":2,"title":"Two","start_block_idx":2},
          {"number":1,"title":"One","start_block_idx":0}
        ]}
        """,
    )

    result = audit_mod.audit_structure_with_llm(structure, blocks, "Book", {})

    assert not result.applied
    assert result.structure is structure
