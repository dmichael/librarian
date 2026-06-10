import json
from pathlib import Path

import subprocess

from librarian.extractors import marker, pdftotext
from librarian.files import (
    marker_content_json,
    marker_html,
    marker_markdown,
    marker_meta_json,
    marker_dir,
    pdftotext_document,
    pdftotext_meta_json,
    pdftotext_pages_json,
    pdftotext_dir,
)


def test_marker_artifact_discovery_uses_raw_marker_dir(tmp_path: Path):
    book_dir = tmp_path / "2"
    raw_marker = book_dir / "raw" / "marker"
    raw_marker.mkdir(parents=True)

    (raw_marker / "document.json").write_text('{"blocks": []}')
    (raw_marker / "document.md").write_text("markdown")
    (raw_marker / "metadata.json").write_text("{}")
    (raw_marker / "document.html").write_text("<html></html>")

    assert marker_dir(book_dir) == raw_marker
    assert marker_content_json(book_dir) == raw_marker / "document.json"
    assert marker_markdown(book_dir) == raw_marker / "document.md"
    assert marker_meta_json(book_dir) == raw_marker / "metadata.json"
    assert marker_html(book_dir) == raw_marker / "document.html"


def test_root_marker_artifacts_are_not_canonical(tmp_path: Path):
    book_dir = tmp_path / "2"
    book_dir.mkdir()

    (book_dir / "2.json").write_text('{"blocks": []}')
    (book_dir / "2.md").write_text("legacy")
    (book_dir / "2_meta.json").write_text("{}")

    assert marker_content_json(book_dir) is None
    assert marker_markdown(book_dir) is None
    assert marker_meta_json(book_dir) is None


def test_pdftotext_artifact_discovery_uses_raw_pdftotext_dir(tmp_path: Path):
    book_dir = tmp_path / "2"
    raw_text = book_dir / "raw" / "pdftotext"
    raw_text.mkdir(parents=True)

    (raw_text / "document.txt").write_text("full text")
    (raw_text / "pages.json").write_text("[]")
    (raw_text / "metadata.json").write_text("{}")

    assert pdftotext_dir(book_dir) == raw_text
    assert pdftotext_document(book_dir) == raw_text / "document.txt"
    assert pdftotext_pages_json(book_dir) == raw_text / "pages.json"
    assert pdftotext_meta_json(book_dir) == raw_text / "metadata.json"


def test_pdftotext_extraction_writes_layout_artifacts(tmp_path: Path, monkeypatch):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    book_dir = tmp_path / "2"
    stale_dir = pdftotext_dir(book_dir)
    stale_dir.mkdir(parents=True)
    (stale_dir / "stale.txt").write_text("old")

    def fake_run(command, check, capture_output, text, encoding, errors, timeout):
        assert command[:4] == ["pdftotext", "-layout", "-enc", "UTF-8"]
        assert command[-1] == "-"
        assert encoding == "utf-8"
        assert errors == "replace"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="page one\n\fpage two\n\f",
            stderr="",
        )

    monkeypatch.setattr("librarian.extractors.pdftotext.subprocess.run", fake_run)

    result = pdftotext.extract(source, book_dir)

    raw_text = pdftotext_dir(book_dir)
    assert result == {"page_count": 2}
    assert (raw_text / "document.txt").read_text() == "page one\n\fpage two\n\f"
    assert json.loads((raw_text / "pages.json").read_text()) == [
        {"page": 1, "text": "page one"},
        {"page": 2, "text": "page two"},
    ]
    assert json.loads((raw_text / "metadata.json").read_text())["tool"] == "pdftotext"
    assert not (raw_text / "stale.txt").exists()


def test_spark_extraction_writes_marker_artifacts_under_raw_marker(tmp_path: Path, monkeypatch):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    book_dir = tmp_path / "2"
    book_dir.mkdir()
    for name in [
        "2.json",
        "2.md",
        "2_meta.json",
        "2.html",
        "2_html_meta.json",
        "_page_1_Figure_3.jpeg",
    ]:
        (book_dir / name).write_text("legacy")

    class FakeResponse:
        is_error = False
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, files, data, timeout):
        if data["output_format"] == "chunks":
            return FakeResponse(
                {
                    "success": True,
                    "output": json.dumps(
                        {
                            "blocks": [
                                {
                                    "block_type": "Text",
                                    "html": "<p>Hello marker</p>",
                                }
                            ]
                        }
                    ),
                    "metadata": {"pages": 1},
                }
            )
        if data["output_format"] == "html":
            return FakeResponse(
                {
                    "success": True,
                    "output": "<html><body>Hello marker</body></html>",
                    "metadata": {"pages": 1},
                    "images": {"page/1/Figure/3.jpeg": "aW1hZ2U="},
                }
            )
        raise AssertionError(f"Unexpected output format: {data['output_format']}")

    monkeypatch.setattr("librarian.extractors.marker.httpx.post", fake_post)

    marker.extract(
        source, book_dir, backend="spark", spark_url="http://test:8001", write_html=True
    )

    raw_marker = marker_dir(book_dir)
    assert (raw_marker / "document.json").exists()
    assert (raw_marker / "document.md").read_text() == "Hello marker"
    assert json.loads((raw_marker / "metadata.json").read_text()) == {"pages": 1}
    assert (raw_marker / "document.html").read_text() == "<html><body>Hello marker</body></html>"
    assert not (raw_marker / "html_metadata.json").exists()
    assert (raw_marker / "images" / "3.jpeg").read_bytes() == b"image"
    assert not (book_dir / "2.json").exists()
    assert not (book_dir / "2.md").exists()
    assert not (book_dir / "2_meta.json").exists()
    assert not (book_dir / "2.html").exists()
    assert not (book_dir / "2_html_meta.json").exists()
    assert not (book_dir / "_page_1_Figure_3.jpeg").exists()
