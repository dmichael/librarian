"""Extraction functions for PDF and EPUB formats.

Pure business logic — each function takes a source file and an output
directory, writes artifacts, and returns. The MCP extract worker is the
caller (librarian.mcp_server).
"""
from __future__ import annotations

import sys
from pathlib import Path

from librarian.config import load_config
from librarian.document_metadata import (
    DocumentMetadata,
    compute_file_hash,
    merge_metadata,
    now_iso,
    save_document_metadata,
)
from librarian.extract_routing import route_pdf



def extract(
    source: Path, output_dir: Path, config: dict | None = None,
) -> tuple[list[str], DocumentMetadata]:
    """Extract a single file to output_dir.

    Returns (errors, metadata). Errors list is empty on full success.
    Writes metadata.json to output_dir.
    """
    suffix = source.suffix.lower()
    source_hash = compute_file_hash(source)

    if suffix == ".pdf":
        errors, meta = extract_pdf(source, output_dir, config)
    elif suffix == ".epub":
        errors, meta = _extract_epub_with_metadata(source, output_dir)
    else:
        return [f"unsupported format: {suffix}"], DocumentMetadata()

    meta.source_hash = source_hash
    meta.source_filename = source.name
    if not meta.format:
        meta.format = suffix.lstrip(".")
    meta.extracted_at = now_iso()

    save_document_metadata(output_dir, meta)
    return errors, meta


def _extract_epub_with_metadata(
    source: Path, output_dir: Path,
) -> tuple[list[str], DocumentMetadata]:
    """Run the ebooklib EPUB extractor, wrapping its result as (errors, DocumentMetadata)."""
    from librarian.epub_extract import extract_epub

    try:
        result = extract_epub(source, output_dir)
    except Exception as e:
        return [f"epub: {e}"], DocumentMetadata(format="epub")

    if not result["success"]:
        return [f"epub: {result['error']}"], DocumentMetadata(format="epub")

    opf = result.get("metadata", {})
    meta = DocumentMetadata(
        format="epub",
        title=opf.get("title"),
        authors=opf.get("authors", []),
        publisher=opf.get("publisher"),
        year=opf.get("year"),
        extractors_run=["epub"],
    )
    return [], meta


def extract_pdf(
    source: Path, output_dir: Path, config: dict | None = None,
) -> tuple[list[str], DocumentMetadata]:
    """Run every PDF extractor over source. Each extractor owns its raw/<name>/.

    Extractors are independent — each is attempted regardless of whether
    others succeed. Returns (errors, merged_metadata).
    """
    from librarian.extractors import grobid, marker

    errors: list[str] = []
    meta_parts: list[DocumentMetadata] = []

    if config is None:
        config = load_config()
    extractors_config = config.get("extractors", {})
    spark_url = extractors_config.get("spark_url")
    grobid_url = extractors_config.get("grobid_url")

    # Misconfiguration, not a runtime hiccup: with neither extractor configured
    # a PDF run would silently produce nothing and still look "extracted". Fail
    # loudly instead so the caller (the MCP worker) surfaces it.
    if not spark_url and not grobid_url:
        raise RuntimeError(
            "extraction not configured: set LIBRARIAN_SPARK_URL (Marker) and/or "
            "GROBID_BASE_URL (GROBID) before extracting PDFs"
        )

    if spark_url:
        decision = route_pdf(source)
        sig = decision.signals
        print(
            f"  ROUTING: {decision.backend} — {decision.reason} "
            f"(img_mpix={sig.img_mpix}, page_mpix={sig.page_megapix}, pages={sig.pages})",
            flush=True,
        )
        # Route oversized scans to Modal (deployed GPU function); everything else
        # to the local Spark. The Modal backend ignores spark_url.
        try:
            marker_result = marker.extract(
                source, output_dir, backend=decision.backend, spark_url=spark_url,
            )
            meta_parts.append(DocumentMetadata(
                format="pdf",
                page_count=marker_result.get("page_count"),
                extractors_run=["marker"],
            ))
        except Exception as e:
            print(f"  MARKER FAILED: {e}", file=sys.stderr, flush=True)
            errors.append(f"marker: {e}")
    else:
        print("  marker: skipped (LIBRARIAN_SPARK_URL not set)", flush=True)

    if grobid_url:
        try:
            print(f"  Extracting fulltext via GROBID ({grobid_url})...", flush=True)
            result = grobid.extract_fulltext(source, output_dir, base_url=grobid_url)
            print(
                f"  GROBID: {len(result.references)} refs, {len(result.citations)} citations, "
                f"{len(result.sections)} sections, {len(result.figures)} figures",
                flush=True,
            )
            # Only trust GROBID's header metadata when it parsed a bibliography
            # (a reliable "this is a scholarly document" signal). On non-papers
            # GROBID's front-matter parse is unreliable and would otherwise win
            # the merge and set a wrong title on every chunk.
            is_paper = bool(result.references)
            meta_parts.append(DocumentMetadata(
                format="pdf",
                title=result.header_title if is_paper else None,
                authors=result.header_authors if is_paper else [],
                year=result.header_year if is_paper else None,
                extractors_run=["grobid"],
            ))
        except Exception as e:
            print(f"  GROBID FAILED: {e}", file=sys.stderr, flush=True)
            errors.append(f"grobid: {e}")
    else:
        print("  grobid: skipped (GROBID_BASE_URL not set)", flush=True)

    meta = merge_metadata(*meta_parts) if meta_parts else DocumentMetadata(format="pdf")
    return errors, meta




