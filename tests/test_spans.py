import json
from pathlib import Path

from librarian import spans
from librarian.files import structure_json, structure_json_path
from librarian.structure import Chapter, DocumentStructure, Section


def _config(tmp_path: Path) -> dict:
    return {
        "output_path": str(tmp_path),
        "public_url": "http://librarian.test",
    }


def _write_marker_blocks(book_dir: Path):
    marker_dir = book_dir / "raw" / "marker"
    marker_dir.mkdir(parents=True)
    (marker_dir / "document.json").write_text(json.dumps({
        "blocks": [
            {"block_type": "SectionHeader", "text": "Chapter One", "page": 1},
            {"block_type": "Text", "text": "alpha " * 20, "page": 1},
            {"block_type": "SectionHeader", "text": "Timing Bands", "page": 2},
            {"block_type": "Text", "text": "timing band " * 30, "page": 2},
            {"block_type": "SectionHeader", "text": "Chapter Two", "page": 5},
            {"block_type": "Text", "text": "beta " * 20, "page": 5},
        ]
    }))


def _structure() -> DocumentStructure:
    structure = DocumentStructure(title="Cycles")
    structure.chapters = [
        Chapter(
            number=1,
            title="Focus on Cycles",
            page_start=1,
            sections=[Section(title="Timing Bands", page_start=2, parent_chapter=1)],
        ),
        Chapter(number=2, title="Tools", page_start=5),
    ]
    structure.block_to_chapter = {0: 1, 1: 1, 2: 1, 3: 1, 4: 2, 5: 2}
    structure.block_to_section = {2: "Timing Bands", 3: "Timing Bands"}
    return structure


def test_structure_artifact_discovery(tmp_path: Path):
    book_dir = tmp_path / "42"
    book_dir.mkdir()
    assert structure_json_path(book_dir) == book_dir / "structure.json"
    assert structure_json(book_dir) is None

    (book_dir / "structure.json").write_text("{}")

    assert structure_json(book_dir) == book_dir / "structure.json"


def test_save_and_list_spans(tmp_path: Path):
    path = spans.save_structure_artifact(
        _config(tmp_path),
        42,
        _structure(),
        source="blocks+llm",
        audit={"applied": True, "reason": "applied 2 chapters"},
        block_count=6,
    )

    assert path == tmp_path / "42" / "structure.json"

    result = spans.list_spans(_config(tmp_path), 42)

    assert result["success"] is True
    assert result["available_scopes"] == ["book", "chapter", "section"]
    assert result["structure_source"] == "blocks+llm"
    assert result["chapters"][0]["start_block_idx"] == 0
    assert result["chapters"][0]["end_block_idx"] == 3
    assert result["chapters"][0]["sections"][0]["start_block_idx"] == 2


def test_read_chapter_span_with_cursor(tmp_path: Path):
    book_dir = tmp_path / "42"
    _write_marker_blocks(book_dir)
    spans.save_structure_artifact(
        _config(tmp_path), 42, _structure(), "blocks+llm", None, block_count=6,
    )

    first = spans.read_span(
        _config(tmp_path), 42, scope="chapter", chapter=1, max_chars=40,
    )

    assert first["success"] is True
    assert first["span"] == {
        "type": "chapter",
        "number": 1,
        "title": "Focus on Cycles",
    }
    assert first["blocks"][0]["block_idx"] == 0
    assert first["blocks"][0]["chapter_num"] == 1
    assert first["next_cursor"] == "block:1"

    second = spans.read_span(
        _config(tmp_path),
        42,
        scope="chapter",
        chapter=1,
        cursor=first["next_cursor"],
        max_chars=500,
    )

    assert second["blocks"][0]["block_idx"] == 1
    assert "timing band" in second["text"]
    assert second["next_cursor"] is None


def test_read_section_span(tmp_path: Path):
    book_dir = tmp_path / "42"
    _write_marker_blocks(book_dir)
    spans.save_structure_artifact(
        _config(tmp_path), 42, _structure(), "blocks+llm", None, block_count=6,
    )

    result = spans.read_span(
        _config(tmp_path), 42, scope="section", section="Timing Bands",
    )

    assert result["success"] is True
    assert result["span"] == {"type": "section", "title": "Timing Bands"}
    assert [block["block_idx"] for block in result["blocks"]] == [2, 3]
    assert all(block["section_title"] == "Timing Bands" for block in result["blocks"])


def test_read_span_requires_structure_artifact(tmp_path: Path):
    assert spans.list_spans(_config(tmp_path), 99) == {
        "success": False,
        "book_id": 99,
        "error": "structure artifact missing; reindex required",
    }

    assert spans.read_span(_config(tmp_path), 99, scope="book") == {
        "success": False,
        "book_id": 99,
        "error": "structure artifact missing; reindex required",
    }
