"""Canonical document metadata — the extraction-stage domain model.

Each extractor populates what it can. The result is written to
metadata.json in the output directory. Downstream stages (indexing,
database) read from this file.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    """Metadata extracted from or about a source document."""

    # Identity
    source_hash: str = ""
    source_filename: str = ""
    format: str = ""

    # Bibliographic
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    publisher: str | None = None
    isbn: str | None = None

    # Structural
    page_count: int | None = None

    # Provenance
    extractors_run: list[str] = Field(default_factory=list)
    extracted_at: str | None = None


def content_hash_hex(file_path: Path) -> str:
    """Truncated SHA-256 hex digest of file contents. Used as directory name."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()[:12]


def compute_file_hash(file_path: Path) -> str:
    """SHA-256 hash with algorithm prefix, for storage in database/metadata."""
    return f"sha256:{content_hash_hex(file_path)}"


def merge_metadata(*sources: DocumentMetadata) -> DocumentMetadata:
    """Merge partial metadata from multiple extractors.

    Scalars: first non-None wins.
    Lists: union (preserving order, deduplicating).
    """
    merged = DocumentMetadata()

    for source in sources:
        if not merged.source_hash and source.source_hash:
            merged.source_hash = source.source_hash
        if not merged.source_filename and source.source_filename:
            merged.source_filename = source.source_filename
        if not merged.format and source.format:
            merged.format = source.format

        if merged.title is None and source.title is not None:
            merged.title = source.title
        for author in source.authors:
            if author not in merged.authors:
                merged.authors.append(author)
        if merged.year is None and source.year is not None:
            merged.year = source.year
        if merged.publisher is None and source.publisher is not None:
            merged.publisher = source.publisher
        if merged.isbn is None and source.isbn is not None:
            merged.isbn = source.isbn

        if merged.page_count is None and source.page_count is not None:
            merged.page_count = source.page_count

        for ext in source.extractors_run:
            if ext not in merged.extractors_run:
                merged.extractors_run.append(ext)

    return merged


METADATA_FILENAME = "metadata.json"


def save_document_metadata(output_dir: Path, metadata: DocumentMetadata) -> Path:
    """Write metadata.json to the output directory."""
    path = output_dir / METADATA_FILENAME
    path.write_text(metadata.model_dump_json(indent=2))
    return path


def load_document_metadata(output_dir: Path) -> DocumentMetadata | None:
    """Read metadata.json from an output directory."""
    path = output_dir / METADATA_FILENAME
    if not path.exists():
        return None
    return DocumentMetadata.model_validate_json(path.read_text())



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
