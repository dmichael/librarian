"""Extraction functions for PDF, EPUB, and Kindle formats.

Pure business logic — no CLI, no Calibre. Each function takes a source
file and an output directory, writes artifacts, and returns.

CLI lives in librarian.cli.extract.
Calibre pipeline glue lives in librarian.calibre.extract.
"""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import markdownify

from librarian.document_metadata import (
    DocumentMetadata,
    compute_file_hash,
    merge_metadata,
    now_iso,
    save_document_metadata,
)
from librarian.files import marker_dir

KINDLE_FORMATS = [".azw3", ".azw", ".mobi", ".kfx"]


def extract(source: Path, output_dir: Path) -> tuple[list[str], DocumentMetadata]:
    """Extract a single file to output_dir.

    Returns (errors, metadata). Errors list is empty on full success.
    Writes metadata.json to output_dir.
    """
    suffix = source.suffix.lower()
    source_hash = compute_file_hash(source)

    if suffix == ".pdf":
        errors, meta = extract_pdf(source, output_dir)
    elif suffix in KINDLE_FORMATS:
        epub_path = convert_to_epub(source, output_dir)
        if epub_path is None:
            meta = DocumentMetadata()
            errors = [f"ebook-convert failed for {source}"]
            return errors, meta
        errors, meta = _extract_epub_with_metadata(epub_path, output_dir)
        meta.format = suffix.lstrip(".")
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
    """Run extract_epub and wrap its result as (errors, DocumentMetadata)."""
    try:
        epub_meta = extract_epub(source, output_dir)
        meta = DocumentMetadata(
            format="epub",
            title=epub_meta.get("title"),
            authors=epub_meta.get("authors", []),
            publisher=epub_meta.get("publisher"),
            year=epub_meta.get("year"),
            extractors_run=["epub"],
        )
        return [], meta
    except Exception as e:
        return [f"epub: {e}"], DocumentMetadata(format="epub")


def extract_pdf(
    source: Path, output_dir: Path,
) -> tuple[list[str], DocumentMetadata]:
    """Run every PDF extractor over source. Each extractor owns its raw/<name>/.

    Extractors are independent — each is attempted regardless of whether
    others succeed. Returns (errors, merged_metadata).
    """
    from librarian.extractors import grobid, marker

    errors: list[str] = []
    meta_parts: list[DocumentMetadata] = []

    spark_url = os.environ.get("LIBRARIAN_SPARK_URL")
    if spark_url:
        try:
            marker_result = marker.extract(
                source, output_dir, backend="spark", spark_url=spark_url,
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

    grobid_url = os.environ.get("GROBID_BASE_URL")
    if grobid_url:
        try:
            print(f"  Extracting fulltext via GROBID ({grobid_url})...", flush=True)
            result = grobid.extract_fulltext(source, output_dir, base_url=grobid_url)
            print(
                f"  GROBID: {len(result.references)} refs, {len(result.citations)} citations, "
                f"{len(result.sections)} sections, {len(result.figures)} figures",
                flush=True,
            )
            meta_parts.append(DocumentMetadata(
                format="pdf",
                title=result.header_title,
                authors=result.header_authors,
                year=result.header_year,
                extractors_run=["grobid"],
            ))
        except Exception as e:
            print(f"  GROBID FAILED: {e}", file=sys.stderr, flush=True)
            errors.append(f"grobid: {e}")
    else:
        print("  grobid: skipped (GROBID_BASE_URL not set)", flush=True)

    meta = merge_metadata(*meta_parts) if meta_parts else DocumentMetadata(format="pdf")
    return errors, meta


def extract_epub(source: Path, output_dir: Path) -> dict:
    """Extract EPUB to markdown by parsing XHTML content.

    Returns a dict of metadata extracted from the OPF (title, authors, etc.).
    """
    raw_dir = marker_dir(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_file = raw_dir / "document.md"
    chapters_dir = output_dir / "chapters"
    chapters_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(source, 'r') as epub:
        container = epub.read('META-INF/container.xml')
        container_tree = ET.fromstring(container)
        ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        opf_path = container_tree.find('.//c:rootfile', ns).get('full-path')
        opf_dir = str(Path(opf_path).parent)
        if opf_dir == '.':
            opf_dir = ''

        opf_content = epub.read(opf_path)
        opf_tree = ET.fromstring(opf_content)

        DC = "http://purl.org/dc/elements/1.1/"
        epub_meta = {
            "title": opf_tree.findtext(f".//{{{DC}}}title"),
            "authors": [
                el.text for el in opf_tree.findall(f".//{{{DC}}}creator")
                if el.text
            ],
            "publisher": opf_tree.findtext(f".//{{{DC}}}publisher"),
        }
        raw_date = opf_tree.findtext(f".//{{{DC}}}date")
        if raw_date:
            import re
            year_match = re.search(r"\d{4}", raw_date)
            if year_match:
                epub_meta["year"] = int(year_match.group(0))

        manifest: dict[str, str] = {}
        for item in opf_tree.findall('.//{http://www.idpf.org/2007/opf}item'):
            item_id = item.get('id')
            href = item.get('href')
            media_type = item.get('media-type', '')
            if item_id and href and ('html' in media_type or 'xhtml' in media_type):
                manifest[item_id] = href

        spine_items: list[str] = []
        for itemref in opf_tree.findall('.//{http://www.idpf.org/2007/opf}itemref'):
            idref = itemref.get('idref')
            if idref and idref in manifest:
                spine_items.append(manifest[idref])

        full_content: list[str] = []
        for i, href in enumerate(spine_items):
            content_path = f"{opf_dir}/{href}" if opf_dir else href

            try:
                html_content = epub.read(content_path).decode('utf-8')
                md_content = markdownify.markdownify(html_content, heading_style="ATX")
                lines = []
                for line in md_content.split('\n'):
                    if line.strip().startswith('xml version='):
                        continue
                    if line.strip() or lines:
                        lines.append(line)
                md_content = '\n'.join(lines).strip()

                full_content.append(md_content)
                chapter_file = chapters_dir / f"{i:03d}.md"
                chapter_file.write_text(md_content)
            except KeyError:
                continue

        output_file.write_text('\n\n---\n\n'.join(full_content))

    return epub_meta


def convert_to_epub(source: Path, output_dir: Path, name: str | None = None) -> Path | None:
    """Convert a file to EPUB using Calibre's ebook-convert."""
    epub_name = name or source.stem
    epub_path = output_dir / f"{epub_name}.epub"

    result = subprocess.run(
        ["ebook-convert", str(source), str(epub_path)],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"ebook-convert failed: {result.stderr}", file=sys.stderr)
        return None

    if not epub_path.exists():
        print("ebook-convert produced no output", file=sys.stderr)
        return None

    return epub_path
