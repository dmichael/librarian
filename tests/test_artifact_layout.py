import json
from pathlib import Path

from librarian.spark_extract import extract_pdf_via_spark
from librarian.extraction_qa import write_extraction_qa
from librarian.files import (
    find_content_json,
    find_html,
    find_markdown,
    find_meta_json,
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
    assert find_content_json(book_dir) == raw_marker / "document.json"
    assert find_markdown(book_dir) == raw_marker / "document.md"
    assert find_meta_json(book_dir) == raw_marker / "metadata.json"
    assert find_html(book_dir) == raw_marker / "document.html"


def test_root_marker_artifacts_are_not_canonical(tmp_path: Path):
    book_dir = tmp_path / "2"
    book_dir.mkdir()

    (book_dir / "2.json").write_text('{"blocks": []}')
    (book_dir / "2.md").write_text("legacy")
    (book_dir / "2_meta.json").write_text("{}")

    assert find_content_json(book_dir) is None
    assert find_markdown(book_dir) is None
    assert find_meta_json(book_dir) is None


def test_extraction_qa_manifest_uses_new_artifact_layout(tmp_path: Path, monkeypatch):
    book_dir = tmp_path / "2"
    raw_marker = marker_dir(book_dir)
    raw_marker.mkdir(parents=True)
    (raw_marker / "document.json").write_text(
        json.dumps(
            {
                "blocks": [
                    {
                        "block_type": "Equation",
                        "html": '<p><math display="block">K = K_o + B\\cos[\\phi(t) + \\phi_i]. \\tag{10}</math></p>',
                    }
                ]
            }
        )
    )
    (raw_marker / "document.md").write_text("K equation")
    (raw_marker / "metadata.json").write_text("{}")
    (raw_marker / "document.html").write_text("<html></html>")
    (raw_marker / "html_metadata.json").write_text("{}")
    image_dir = raw_marker / "images"
    image_dir.mkdir()
    (image_dir / "_page_1_Figure_3.jpeg").write_bytes(b"image")

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")

    def fake_run(self, source_path, output_dir):
        pdftext_dir = output_dir / "raw" / "pdftext"
        pdftext_dir.mkdir(parents=True)
        path = pdftext_dir / "layout.txt"
        path.write_text("K 苷 Ko 1 B cos关f共t兲 1 fj 兴 .               (10)")
        from librarian.extractors import ExtractorResult

        return ExtractorResult(name="pdftext", success=True, output_path=str(path))

    monkeypatch.setattr("librarian.extractors.PdfTextExtractor.run", fake_run)

    result = write_extraction_qa(source, book_dir)
    manifest = json.loads((book_dir / "review" / "artifacts.json").read_text())

    assert result.success
    assert result.raw_outputs == {"pdftext": "raw/pdftext/layout.txt"}
    artifact_paths = {item["role"]: item["path"] for item in manifest["artifacts"]}
    assert artifact_paths["marker_json"] == "raw/marker/document.json"
    assert artifact_paths["marker_markdown"] == "raw/marker/document.md"
    assert artifact_paths["marker_html"] == "raw/marker/document.html"
    assert artifact_paths["pdftext_layout"] == "raw/pdftext/layout.txt"
    assert manifest["images"] == [
        {"path": "raw/marker/images/_page_1_Figure_3.jpeg", "bytes": 5}
    ]


def test_spark_extraction_writes_marker_artifacts_under_raw_marker(tmp_path: Path, monkeypatch):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    book_dir = tmp_path / "2"

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

    monkeypatch.setattr("librarian.spark_extract.httpx.post", fake_post)
    monkeypatch.setattr("librarian.spark_extract._write_qa_artifacts", lambda source, output: None)

    assert extract_pdf_via_spark(source, book_dir, write_html=True)

    raw_marker = marker_dir(book_dir)
    assert (raw_marker / "document.json").exists()
    assert (raw_marker / "document.md").read_text() == "Hello marker"
    assert json.loads((raw_marker / "metadata.json").read_text()) == {"pages": 1}
    assert (raw_marker / "document.html").read_text() == "<html><body>Hello marker</body></html>"
    assert json.loads((raw_marker / "html_metadata.json").read_text()) == {"pages": 1}
    assert (raw_marker / "images" / "3.jpeg").read_bytes() == b"image"
    assert not (book_dir / "2.json").exists()
    assert not (book_dir / "2.md").exists()
