"""GROBID extractor.

Writes the raw TEI XML returned by GROBID's /api/processReferences
endpoint to raw/grobid/references.tei.xml. The TEI→CSL conversion and
the clean/references.csl.json output live in librarian.references (the
references domain builder); this module is only the extractor.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx


NAME = "grobid"
ARTIFACT_REL_PATH = Path("raw") / NAME / "references.tei.xml"

DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_CONSOLIDATE_CITATIONS = "0"

# An empty-but-well-formed TEI listBibl, returned when GROBID can't find any
# references (HTTP 204). Downstream code parses TEI regardless of source, so
# we keep the artifact present and let "zero references" be valid output.
EMPTY_LIST_BIBL_TEI = '<listBibl xmlns="http://www.tei-c.org/ns/1.0" />'


def resolve_base_url(explicit: str | None = None) -> str:
    """Resolve the GROBID service root URL.

    Raises ValueError if neither argument nor GROBID_BASE_URL is set.
    """
    if explicit:
        return explicit.rstrip("/")
    if base_url := os.getenv("GROBID_BASE_URL"):
        return base_url.rstrip("/")
    raise ValueError("Set GROBID_BASE_URL to the GROBID service root, e.g. http://host:8070")


def extract(
    source: Path,
    book_dir: Path,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    consolidate_citations: str = DEFAULT_CONSOLIDATE_CITATIONS,
) -> None:
    """Call GROBID's processReferences, write TEI XML to raw/grobid/.

    Raises on any failure (unreachable service, non-2xx that isn't 204, etc).
    HTTP 204 (no references found) is treated as valid output — an empty
    listBibl document is written so downstream parsing always has a file.
    """
    if not source.exists():
        raise FileNotFoundError(source)

    url = f"{resolve_base_url(base_url)}/api/processReferences"

    with source.open("rb") as pdf:
        response = httpx.post(
            url,
            headers={"Accept": "application/xml"},
            files={"input": (source.name, pdf, "application/pdf")},
            data={
                "includeRawCitations": "1",
                "consolidateCitations": consolidate_citations,
            },
            timeout=timeout,
        )

    if response.status_code == 204:
        tei = EMPTY_LIST_BIBL_TEI
    else:
        response.raise_for_status()
        tei = response.text

    out_path = book_dir / ARTIFACT_REL_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tei)
