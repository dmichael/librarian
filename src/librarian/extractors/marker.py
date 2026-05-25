"""Marker extractor.

Writes Marker's chunks/markdown/HTML/images/metadata to raw/marker/.

Backends:
  - "spark": POST to the Spark marker HTTP service (default; LAN GPU).

The cloud (Modal) backend is a batch operation by nature and stays in
librarian.cloud_extract for now; it will be aligned to this interface in
a later pass.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path

import httpx

from librarian.files import chunks_to_markdown, marker_dir


NAME = "marker"

DEFAULT_SPARK_URL = "http://spark-f80b.local:8001"
DEFAULT_TIMEOUT_SECONDS = 1800  # 30 min per book


class MarkerExtractionError(RuntimeError):
    """Raised when the marker service fails to extract a PDF."""


def extract(
    source: Path,
    book_dir: Path,
    *,
    backend: str = "spark",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    write_html: bool = True,
) -> None:
    """Extract source into book_dir/raw/marker/. Raises on any failure.

    Produces:
      - raw/marker/document.json       chunks (block list)
      - raw/marker/metadata.json       document metadata
      - raw/marker/document.md         markdown rendered by chunks_to_markdown

    When write_html=True, also produces (from a second Marker pass):
      - raw/marker/document.html       rendered HTML
      - raw/marker/html_metadata.json  HTML-pass metadata
      - raw/marker/images/*            JPEG/PNG payloads used by the HTML
    """
    if backend == "spark":
        _extract_via_spark(source, book_dir, timeout=timeout, write_html=write_html)
    elif backend == "cloud":
        raise NotImplementedError(
            "marker cloud backend is not yet exposed through extract(); "
            "use librarian.cloud_extract.extract_books_cloud for batch runs"
        )
    else:
        raise ValueError(f"Unknown marker backend: {backend!r}")


# ---------------------------------------------------------------------------
# Spark backend
# ---------------------------------------------------------------------------


def _spark_url() -> str:
    return os.environ.get("LIBRARIAN_SPARK_URL", DEFAULT_SPARK_URL).rstrip("/")


def _extract_via_spark(
    source: Path,
    book_dir: Path,
    *,
    timeout: int,
    write_html: bool,
) -> None:
    url = f"{_spark_url()}/marker/upload"
    _prepare_output_layout(book_dir)
    marker_output_dir = marker_dir(book_dir)
    marker_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Extracting via Spark marker service ({url})...", flush=True)

    payload = _post_to_spark(url, source, output_format="chunks", timeout=timeout)

    output_raw = payload.get("output")
    if not output_raw:
        raise MarkerExtractionError("Spark response missing 'output' field")

    try:
        chunks_data = json.loads(output_raw)
    except json.JSONDecodeError as e:
        raise MarkerExtractionError(f"Spark chunks JSON malformed: {e}") from e

    chunks_path = marker_output_dir / "document.json"
    chunks_path.write_text(json.dumps(chunks_data, indent=2))
    (marker_output_dir / "metadata.json").write_text(
        json.dumps(payload.get("metadata", {}), indent=2)
    )
    (marker_output_dir / "document.md").write_text(chunks_to_markdown(chunks_path))

    if write_html:
        _write_html_artifacts(source, book_dir, timeout)


def _post_to_spark(
    url: str, source: Path, *, output_format: str, timeout: int
) -> dict:
    """POST a PDF to the Spark service and return the parsed payload.

    Raises MarkerExtractionError on any failure: connection, HTTP status,
    non-JSON response, or service-level success=False.
    """
    try:
        with open(source, "rb") as fh:
            response = httpx.post(
                url,
                files={"file": (source.name, fh, "application/pdf")},
                data={"output_format": output_format},
                timeout=timeout,
            )
    except httpx.HTTPError as e:
        raise MarkerExtractionError(f"Spark request failed: {e}") from e

    if response.is_error:
        raise MarkerExtractionError(
            f"Spark returned HTTP {response.status_code}: {response.text[:300]}"
        )

    try:
        payload = response.json()
    except ValueError as e:
        raise MarkerExtractionError(f"Spark response was not JSON: {e}") from e

    if not payload.get("success"):
        raise MarkerExtractionError(
            f"Spark extraction failed: {payload.get('error', 'unknown error')}"
        )

    return payload


def _write_html_artifacts(source: Path, book_dir: Path, timeout: int) -> None:
    """Write Marker HTML companion artifact + images. Raises on any failure."""
    url = f"{_spark_url()}/marker/upload"
    marker_output_dir = marker_dir(book_dir)
    image_dir = marker_output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    print("  Requesting HTML companion artifact for review...", flush=True)

    payload = _post_to_spark(url, source, output_format="html", timeout=timeout)

    html = payload.get("output")
    if not html:
        raise MarkerExtractionError("Spark HTML response missing 'output' field")

    (marker_output_dir / "document.html").write_text(html)
    (marker_output_dir / "html_metadata.json").write_text(
        json.dumps(payload.get("metadata", {}), indent=2)
    )

    for name, encoded in (payload.get("images") or {}).items():
        image_path = image_dir / Path(name).name
        image_path.write_bytes(base64.b64decode(encoded))


def _prepare_output_layout(book_dir: Path) -> None:
    """Clear stale marker artifacts before a fresh extraction.

    Removes legacy root-level marker files from the pre-raw/ layout, and
    wipes the current raw/marker/ directory. Source PDFs live elsewhere
    and are never touched here.
    """
    book_dir.mkdir(parents=True, exist_ok=True)
    book_id = book_dir.name

    legacy_files = [
        book_dir / f"{book_id}.json",
        book_dir / f"{book_id}.md",
        book_dir / f"{book_id}_meta.json",
        book_dir / f"{book_id}.html",
        book_dir / f"{book_id}_html_meta.json",
    ]
    legacy_files.extend(book_dir.glob("_page_*"))

    for path in legacy_files:
        if path.is_file():
            path.unlink()

    raw_marker = marker_dir(book_dir)
    if raw_marker.exists():
        shutil.rmtree(raw_marker)
