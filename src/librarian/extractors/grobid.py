"""GROBID extractor.

Writes raw TEI XML to raw/grobid/references.tei.xml. The TEI→CSL conversion
and clean/references.csl.json output live in librarian.references (the
references domain builder); this module is only the extractor.
"""

from __future__ import annotations

from pathlib import Path

import httpx


def extract(
    source: Path,
    book_dir: Path,
    *,
    base_url: str,
    timeout: float = 180.0,
    consolidate_citations: str = "0",
) -> None:
    """POST source to GROBID /api/processReferences, write TEI to raw/grobid/.

    Raises on any failure (unreachable service, non-2xx that isn't 204, etc).
    HTTP 204 (no references found) writes an empty listBibl document so
    downstream parsing always has a file.
    """
    if not source.exists():
        raise FileNotFoundError(source)

    with source.open("rb") as pdf:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/processReferences",
            headers={"Accept": "application/xml"},
            files={"input": (source.name, pdf, "application/pdf")},
            data={
                "includeRawCitations": "1",
                "consolidateCitations": consolidate_citations,
            },
            timeout=timeout,
        )

    if response.status_code == 204:
        tei = '<listBibl xmlns="http://www.tei-c.org/ns/1.0" />'
    else:
        response.raise_for_status()
        tei = response.text

    out_path = book_dir / "raw" / "grobid" / "references.tei.xml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tei)
