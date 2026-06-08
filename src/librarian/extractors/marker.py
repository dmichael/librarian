"""Marker extractor.

Writes Marker's native artifacts AND normalized views to raw/marker/. Marker
emits HTML-wrapped LaTeX for equations, with three different inconsistent
number formats (\\tag{N}, inline (N), post-math (N)); the normalization
into raw/marker/equations.json is owned here because only this module is
allowed to know Marker's quirks.

Backends:
  - "spark": POST to the Spark marker HTTP service (LAN GPU).
"""

from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from librarian.files import chunks_to_markdown, marker_dir


class MarkerExtractionError(RuntimeError):
    """Raised when the marker service fails to extract a PDF."""


class Equation(BaseModel):
    """A single equation parsed from a Marker block."""

    model_config = ConfigDict(extra="forbid")

    block_index: int
    page: int | None = None
    number: str | None = None
    latex: str


_MATH_RE = re.compile(r"<math[^>]*>(.*?)</math>", re.DOTALL)
_TAG_RE = re.compile(r"\\tag\{([^}]+)\}")
_TRAILING_PAREN_RE = re.compile(r"\(([0-9]+)\)\s*$")
_OUTSIDE_PAREN_RE = re.compile(r"\(([0-9]+)\)")


def extract(
    source: Path,
    book_dir: Path,
    *,
    backend: str = "spark",
    spark_url: str | None = None,
    timeout: int = 1800,
    write_html: bool = True,
) -> dict:
    """Extract source into book_dir/raw/marker/. Raises on any failure.

    Returns a dict with extractor metadata (e.g. {"page_count": N}).

    Produces:
      - raw/marker/document.json       chunks (block list)
      - raw/marker/metadata.json       document metadata
      - raw/marker/document.md         markdown rendered by chunks_to_markdown

    When write_html=True, also produces (from a second Marker pass):
      - raw/marker/document.html       rendered HTML
      - raw/marker/images/*            JPEG/PNG payloads used by the HTML

    Note: Marker's HTML-pass metadata is byte-identical to the chunks-pass
    metadata.json, so we don't write it twice.
    """
    if backend == "spark":
        if not spark_url:
            raise ValueError("spark_url is required for backend='spark'")
        return _extract_via_spark(
            source, book_dir, spark_url=spark_url, timeout=timeout, write_html=write_html
        )
    else:
        raise ValueError(f"Unknown marker backend: {backend!r}")


# ---------------------------------------------------------------------------
# Spark backend
# ---------------------------------------------------------------------------


def _extract_via_spark(
    source: Path,
    book_dir: Path,
    *,
    spark_url: str,
    timeout: int,
    write_html: bool,
) -> dict:
    url = f"{spark_url.rstrip('/')}/marker/upload"
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

    equations = parse_equations(chunks_data.get("blocks", []))
    (marker_output_dir / "equations.json").write_text(
        json.dumps([eq.model_dump(exclude_none=True) for eq in equations], indent=2) + "\n"
    )

    if write_html:
        _write_html_artifacts(url, source, book_dir, timeout)

    marker_meta = payload.get("metadata", {})
    page_count = marker_meta.get("page_count") or len(marker_meta.get("page_stats", []))
    return {"page_count": page_count or None}


def _post_to_spark(
    url: str, source: Path, *, output_format: str, timeout: int
) -> dict:
    """POST a PDF to the Spark service and return the parsed payload."""
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


def _write_html_artifacts(url: str, source: Path, book_dir: Path, timeout: int) -> None:
    """Write Marker HTML companion artifact + images. Raises on any failure."""
    marker_output_dir = marker_dir(book_dir)
    image_dir = marker_output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    print("  Requesting HTML companion artifact for review...", flush=True)

    payload = _post_to_spark(url, source, output_format="html", timeout=timeout)

    html = payload.get("output")
    if not html:
        raise MarkerExtractionError("Spark HTML response missing 'output' field")

    (marker_output_dir / "document.html").write_text(html)

    for name, encoded in (payload.get("images") or {}).items():
        (image_dir / Path(name).name).write_bytes(base64.b64decode(encoded))


def parse_equations(blocks: list[dict]) -> list[Equation]:
    """Pull equation blocks out of Marker's chunks JSON into a flat typed list.

    Marker emits equation numbers three ways and we have to handle all of them:
      1. \\tag{N} inside the <math> element                 (a = ..., \\tag{1})
      2. trailing (N) inside the <math> element             (a = ... (3))
      3. (N) AFTER the </math> close tag, in the <p> wrap   (<math>a = ...</math> (4))

    These are Marker output quirks; this is the only place that should care.
    """
    equations: list[Equation] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        if str(block.get("block_type", "")).lower() != "equation":
            continue
        if eq := _parse_equation_block(block, index):
            equations.append(eq)
    return equations


def _parse_equation_block(block: dict, block_index: int) -> Equation | None:
    html = block.get("html") or ""
    math_match = _MATH_RE.search(html)
    if not math_match:
        return None
    math = math_match.group(1).strip()

    number: str | None = None
    latex = math
    if tag_match := _TAG_RE.search(math):
        number = tag_match.group(1)
        latex = math[: tag_match.start()].strip()
    elif inline := _TRAILING_PAREN_RE.search(math):
        number = inline.group(1)
        latex = math[: inline.start()].strip()
    elif after := _OUTSIDE_PAREN_RE.search(html[math_match.end():]):
        number = after.group(1)
        latex = math

    latex = latex.rstrip(",").rstrip(".").rstrip().strip()
    if not latex:
        return None

    page = block.get("page")
    return Equation(
        block_index=block_index,
        page=page if isinstance(page, int) else None,
        number=number,
        latex=latex,
    )


def _prepare_output_layout(book_dir: Path) -> None:
    """Clear stale marker artifacts before a fresh extraction."""
    book_dir.mkdir(parents=True, exist_ok=True)
    book_id = book_dir.name

    legacy_files = [
        book_dir / f"{book_id}.json",
        book_dir / f"{book_id}.md",
        book_dir / f"{book_id}_meta.json",
        book_dir / f"{book_id}.html",
        book_dir / f"{book_id}_html_meta.json",
        marker_dir(book_dir) / "html_metadata.json",  # was written until 2026-05-25
    ]
    legacy_files.extend(book_dir.glob("_page_*"))

    for path in legacy_files:
        if path.is_file():
            path.unlink()

    raw_marker = marker_dir(book_dir)
    if raw_marker.exists():
        shutil.rmtree(raw_marker)
