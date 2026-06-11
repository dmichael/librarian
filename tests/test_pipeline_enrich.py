"""Tests for the automatic enrichment hook in the extraction worker."""

from pathlib import Path

from librarian import pipeline
from librarian.document_metadata import DocumentMetadata
from librarian.extract import ExtractionResult


def _run_worker(tmp_path: Path, monkeypatch, *, enrich_fn):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF")

    status_calls = []
    monkeypatch.setattr(
        "librarian.pipeline.update_book_status",
        lambda book_id, status, message=None, config=None, **f:
            status_calls.append((status, message)),
    )
    monkeypatch.setattr(
        "librarian.pipeline.update_book_fields", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "librarian.extract.extract",
        lambda source, output_dir, config, status_fn=None: ExtractionResult(
            errors=[],
            metadata=DocumentMetadata(format="pdf", extraction_backend="modal"),
            has_content=True,
        ),
    )
    monkeypatch.setattr("librarian.enrich.enrich_book", enrich_fn)

    pipeline.extract_worker(7, str(pdf), str(tmp_path / "out"), {})
    return status_calls


def test_worker_enriches_after_successful_extraction(tmp_path, monkeypatch):
    enrich_calls = []

    def fake_enrich(book_id, config, force=False, **kwargs):
        enrich_calls.append((book_id, force))
        return {"status": "enriched", "changes": ["title: a → b"], "message": ""}

    status_calls = _run_worker(tmp_path, monkeypatch, enrich_fn=fake_enrich)

    # Runs once, with force — even complete-looking metadata gets enriched.
    assert enrich_calls == [(7, True)]
    assert status_calls[-1][0] == "extracted"


def test_enrichment_failure_does_not_fail_the_book(tmp_path, monkeypatch):
    def broken_enrich(*a, **k):
        raise RuntimeError("isbn lookup down")

    status_calls = _run_worker(tmp_path, monkeypatch, enrich_fn=broken_enrich)

    assert status_calls[-1][0] == "extracted"
