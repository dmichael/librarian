import json
from pathlib import Path

from librarian.extractors import marker
from librarian.files import (
    marker_content_json,
    marker_html,
    marker_markdown,
    marker_meta_json,
    marker_dir,
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
    assert json.loads((raw_marker / "html_metadata.json").read_text()) == {"pages": 1}
    assert (raw_marker / "images" / "3.jpeg").read_bytes() == b"image"
    assert not (book_dir / "2.json").exists()
    assert not (book_dir / "2.md").exists()
    assert not (book_dir / "2_meta.json").exists()
    assert not (book_dir / "2.html").exists()
    assert not (book_dir / "2_html_meta.json").exists()
    assert not (book_dir / "_page_1_Figure_3.jpeg").exists()
