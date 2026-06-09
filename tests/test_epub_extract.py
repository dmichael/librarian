"""End-to-end test for the unified EPUB extraction path.

Builds a real EPUB with ebooklib, runs librarian.extract.extract on it, and
checks both the indexable artifacts (document.json blocks, document.md) and
the persisted metadata (OPF title/authors/year into metadata.json).
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("ebooklib")

from ebooklib import epub

from librarian.document_metadata import load_document_metadata
from librarian.extract import extract
from librarian.files import marker_dir


@pytest.fixture
def sample_epub(tmp_path: Path) -> Path:
    book = epub.EpubBook()
    book.set_identifier("test-epub-001")
    book.set_title("The Test Book")
    book.set_language("en")
    book.add_author("Ada Example")
    book.add_metadata("DC", "publisher", "Test Press")
    book.add_metadata("DC", "date", "2021-05-01")

    chapter = epub.EpubHtml(title="Chapter 1", file_name="ch1.xhtml", lang="en")
    chapter.content = (
        "<html><body><h1>Chapter 1</h1>"
        "<p>Photosynthesis converts light into chemical energy.</p>"
        "</body></html>"
    )
    book.add_item(chapter)
    book.toc = [chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]

    epub_path = tmp_path / "test.epub"
    epub.write_epub(str(epub_path), book)
    return epub_path


def test_extract_epub_end_to_end(sample_epub: Path, tmp_path: Path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    errors, meta = extract(sample_epub, output_dir)

    assert errors == []

    # Indexable artifacts: blocks + markdown
    raw_dir = marker_dir(output_dir)
    blocks = json.loads((raw_dir / "document.json").read_text())["blocks"]
    assert any(b["block_type"] == "SectionHeader" for b in blocks)
    assert any("Photosynthesis" in b["html"] for b in blocks)
    assert "Photosynthesis" in (raw_dir / "document.md").read_text()

    # OPF metadata returned and persisted to metadata.json
    assert meta.title == "The Test Book"
    assert meta.authors == ["Ada Example"]
    assert meta.publisher == "Test Press"
    assert meta.year == 2021

    saved = load_document_metadata(output_dir)
    assert saved is not None
    assert saved.title == "The Test Book"
    assert saved.format == "epub"
    assert saved.source_hash


def test_extract_unsupported_format(tmp_path: Path):
    bad = tmp_path / "notes.txt"
    bad.write_text("plain text")

    errors, meta = extract(bad, tmp_path / "out2")

    assert errors and "unsupported format" in errors[0]
