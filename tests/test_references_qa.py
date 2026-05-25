import json
from pathlib import Path

from librarian.references_qa import (
    build_references_qa,
    extract_visible_references_from_marker_json,
    write_references_qa,
)


def test_extract_visible_references_from_marker_json_reads_marker_list_items(tmp_path: Path):
    marker_json = tmp_path / "document.json"
    marker_json.write_text(
        json.dumps(
            {
                "blocks": [
                    {"block_type": "Text", "html": "<p>body [1]</p>"},
                    {
                        "block_type": "ListGroup",
                        "html": (
                            "<ul>"
                            "<li>*Present address</li>"
                            "<li>[1] F. Nottebohm, Behav. Neural. Biol. 46, 445 (1986).</li>"
                            "<li>[2] M. Brainard and A. Doupe, Nature Rev. 1, 31 (2000).</li>"
                            "</ul>"
                        ),
                    },
                ]
            }
        )
    )

    refs = extract_visible_references_from_marker_json(marker_json)

    assert [item.label for item in refs] == [1, 2]
    assert refs[0].source_block == 1
    assert refs[0].text.startswith("F. Nottebohm")


def test_build_references_qa_flags_count_and_extra_structured_label(tmp_path: Path):
    _write_marker_json(
        tmp_path,
        [
            "[1] Alpha.",
            "[2] Beta includes See Gamma.",
            "[3] Delta.",
        ],
    )
    _write_csl_json(
        tmp_path,
        [
            {"id": "ref-1", "title": "Alpha"},
            {"id": "ref-2", "title": "Beta"},
            {"id": "ref-3", "title": "See Gamma."},
            {"id": "ref-4", "title": "Delta"},
        ],
    )

    result = build_references_qa(tmp_path)

    assert result.marker_count == 3
    assert result.csl_count == 4
    assert result.extra_structured_labels == [4]
    assert [issue.code for issue in result.issues] == [
        "reference_count_mismatch",
        "extra_structured_labels",
    ]


def test_write_references_qa_writes_json_and_markdown(tmp_path: Path):
    _write_marker_json(tmp_path, ["[1] Alpha.", "[3] Gamma."])
    _write_csl_json(tmp_path, [{"id": "ref-1"}, {"id": "ref-3"}])

    result = write_references_qa(tmp_path)

    assert result.missing_marker_labels == [2]
    report = json.loads((tmp_path / "review" / "references_qa.json").read_text())
    assert report["missing_marker_labels"] == [2]
    markdown = (tmp_path / "review" / "references_qa.md").read_text()
    assert "`missing_marker_labels`" in markdown


def _write_marker_json(book_dir: Path, items: list[str]) -> None:
    marker_dir = book_dir / "raw" / "marker"
    marker_dir.mkdir(parents=True)
    html_items = "".join(f"<li>{item}</li>" for item in items)
    (marker_dir / "document.json").write_text(
        json.dumps({"blocks": [{"block_type": "ListGroup", "html": f"<ul>{html_items}</ul>"}]})
    )


def _write_csl_json(book_dir: Path, refs: list[dict]) -> None:
    clean_dir = book_dir / "clean"
    clean_dir.mkdir(parents=True)
    (clean_dir / "references.csl.json").write_text(json.dumps(refs))
