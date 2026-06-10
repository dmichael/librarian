"""Tests for metadata gating in extract_pdf."""

from pathlib import Path

from librarian.extractors.grobid import CSLReference, FulltextResult
from librarian import extract as extract_mod


def _fake_result(*, with_refs: bool) -> FulltextResult:
    return FulltextResult(
        references=[CSLReference(id="b0", type="article-journal")] if with_refs else [],
        citations=[],
        sections=[],
        figures=[],
        header_title="Parsed Header Title",
        header_authors=["A. Author"],
        header_year=2020,
    )


def _run(tmp_path: Path, monkeypatch, *, with_refs: bool):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")

    # Only GROBID runs (no Spark URL → marker skipped).
    monkeypatch.delenv("LIBRARIAN_SPARK_URL", raising=False)
    monkeypatch.setenv("GROBID_BASE_URL", "http://grobid.test:8070")
    monkeypatch.setattr(
        "librarian.extractors.grobid.extract_fulltext",
        lambda *a, **k: _fake_result(with_refs=with_refs),
    )

    errors, meta = extract_mod.extract_pdf(pdf, tmp_path)
    return meta


def test_grobid_header_adopted_for_papers(tmp_path: Path, monkeypatch):
    meta = _run(tmp_path, monkeypatch, with_refs=True)
    assert meta.title == "Parsed Header Title"
    assert meta.authors == ["A. Author"]
    assert meta.year == 2020


def test_extract_pdf_requires_a_configured_extractor(tmp_path: Path, monkeypatch):
    import pytest

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.delenv("LIBRARIAN_SPARK_URL", raising=False)
    monkeypatch.delenv("GROBID_BASE_URL", raising=False)

    # Neither extractor configured → loud failure, not a silent empty "success".
    with pytest.raises(RuntimeError, match="extraction not configured"):
        extract_mod.extract_pdf(pdf, tmp_path)


def test_grobid_header_ignored_without_references(tmp_path: Path, monkeypatch):
    # No bibliography → likely not a paper → don't trust GROBID's front matter.
    meta = _run(tmp_path, monkeypatch, with_refs=False)
    assert meta.title is None
    assert meta.authors == []
    assert meta.year is None


def test_extraction_backend_recorded(tmp_path: Path, monkeypatch):
    # The marker routing decision (spark/modal) is recorded on the metadata so
    # it's visible in book_status without digging through container logs.
    from librarian.extract_routing import PdfSignals, RoutingDecision

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")

    decision = RoutingDecision(
        backend="modal",
        reason="img_mpix 1200 > 900",
        signals=PdfSignals(pages=400, img_mpix=1200.0, page_megapix=13.0, size_mb=21.0),
    )
    monkeypatch.setattr("librarian.extract.route_pdf", lambda p: decision)
    monkeypatch.setattr(
        "librarian.extractors.marker.extract", lambda *a, **k: {"page_count": 400}
    )

    # Spark configured (marker runs), no GROBID.
    config = {"extractors": {"spark_url": "http://spark.test:8001"}}
    errors, meta = extract_mod.extract_pdf(pdf, tmp_path, config)

    assert errors == []
    assert meta.extraction_backend == "modal"
