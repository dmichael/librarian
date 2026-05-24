"""Extract PDFs via the marker HTTP service running on the DGX Spark.

This module is the local-network equivalent of cloud_extract.py: instead
of dispatching to Modal A100s, it POSTs each PDF to the Spark's marker
HTTP service at `http://spark-f80b.local:8001/marker/upload` and writes
the response to disk in the same layout the rest of the pipeline expects.

Override the host via the `LIBRARIAN_SPARK_URL` env var if the Spark
moves or you point this at a different host.
"""

import base64
import json
import os
import shutil
import sys
from pathlib import Path

import httpx

from librarian.files import marker_dir


DEFAULT_SPARK_URL = "http://spark-f80b.local:8001"
DEFAULT_TIMEOUT_SECONDS = 1800  # 30 min per book


def get_spark_url() -> str:
    return os.environ.get("LIBRARIAN_SPARK_URL", DEFAULT_SPARK_URL).rstrip("/")


def extract_pdf_via_spark(
    source: Path,
    output_dir: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    write_html: bool = True,
) -> bool:
    """POST a PDF to the Spark marker service and write chunks to output_dir.

    Produces raw Marker artifacts under raw/marker:
      - document.json       chunks (block list)
      - metadata.json       document metadata
      - document.md         markdown rendered by _chunks_to_markdown

    When write_html=True, also writes human-review artifacts from a second
    Marker pass:
      - document.html          rendered HTML
      - html_metadata.json     HTML-pass metadata
      - images/*               JPEG/PNG payloads used by the HTML

    Args:
        source: Path to PDF file
        output_dir: Per-book output directory (its name is the book id)
        timeout: HTTP request timeout in seconds
        write_html: Also request Marker HTML output for human QA

    Returns:
        True on successful extraction and write, False on any failure.
    """
    url = f"{get_spark_url()}/marker/upload"
    _prepare_output_layout(output_dir)
    marker_output_dir = marker_dir(output_dir)
    marker_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Extracting via Spark marker service ({url})...", flush=True)

    try:
        with open(source, "rb") as fh:
            response = httpx.post(
                url,
                files={"file": (source.name, fh, "application/pdf")},
                data={"output_format": "chunks"},
                timeout=timeout,
            )
    except httpx.HTTPError as e:
        print(f"  Spark request failed: {e}", file=sys.stderr)
        return False

    if response.is_error:
        print(
            f"  Spark returned HTTP {response.status_code}: {response.text[:300]}",
            file=sys.stderr,
        )
        return False

    try:
        payload = response.json()
    except ValueError as e:
        print(f"  Spark response was not JSON: {e}", file=sys.stderr)
        return False

    if not payload.get("success"):
        print(
            f"  Spark extraction failed: {payload.get('error', 'unknown error')}",
            file=sys.stderr,
        )
        return False

    # `output` is a JSON-encoded string when output_format=chunks.
    output_raw = payload.get("output")
    if not output_raw:
        print("  Spark response missing 'output' field", file=sys.stderr)
        return False

    try:
        chunks_data = json.loads(output_raw)
    except json.JSONDecodeError as e:
        print(f"  Spark chunks JSON malformed: {e}", file=sys.stderr)
        return False

    # Write raw Marker content + metadata to the canonical raw extractor path.
    chunks_path = marker_output_dir / "document.json"
    chunks_path.write_text(json.dumps(chunks_data, indent=2))
    (marker_output_dir / "metadata.json").write_text(
        json.dumps(payload.get("metadata", {}), indent=2)
    )

    # Also write the human-readable markdown rendering, matching what the
    # local + cloud paths produce. Imported here (not module-top) to keep
    # spark_extract self-contained for callers that only want the chunks
    # write. The leading underscore on _chunks_to_markdown is intentional
    # private-within-package — we're a sibling, not an external user.
    from librarian.extract import _chunks_to_markdown
    (marker_output_dir / "document.md").write_text(_chunks_to_markdown(chunks_path))

    if write_html:
        _write_html_artifacts(source, output_dir, timeout)

    _write_qa_artifacts(source, output_dir)

    return True


def _prepare_output_layout(output_dir: Path) -> None:
    """Prepare the per-book artifact directory for a fresh raw extraction.

    This removes generated artifacts from the old root-level layout and clears
    the current raw Marker directory so a re-extraction cannot leave stale files
    behind. Source PDFs live elsewhere and are never touched here.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    book_id = output_dir.name

    legacy_files = [
        output_dir / f"{book_id}.json",
        output_dir / f"{book_id}.md",
        output_dir / f"{book_id}_meta.json",
        output_dir / f"{book_id}.html",
        output_dir / f"{book_id}_html_meta.json",
    ]
    legacy_files.extend(output_dir.glob("_page_*"))

    for path in legacy_files:
        if path.is_file():
            path.unlink()

    raw_marker = marker_dir(output_dir)
    if raw_marker.exists():
        shutil.rmtree(raw_marker)


def _write_qa_artifacts(source: Path, output_dir: Path) -> None:
    """Write extraction QA artifacts if local baseline tools are available."""
    try:
        from librarian.extraction_qa import write_extraction_qa

        result = write_extraction_qa(source, output_dir)
    except Exception as e:
        print(f"  Extraction QA failed: {e}", file=sys.stderr)
        return

    if result.success:
        print(
            f"  Wrote extraction QA report ({result.findings} findings) to {result.review_dir}",
            flush=True,
        )
    else:
        print(f"  Extraction QA skipped: {result.error}", file=sys.stderr)


def _write_html_artifacts(source: Path, output_dir: Path, timeout: int) -> None:
    """Write Marker HTML output beside the canonical chunks artifacts.

    HTML is a human-QA convenience, not the canonical indexing artifact. If
    Marker fails to produce it, log the issue but leave the extraction usable.
    """
    url = f"{get_spark_url()}/marker/upload"
    marker_output_dir = marker_dir(output_dir)
    image_dir = marker_output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    print("  Requesting HTML companion artifact for review...", flush=True)

    try:
        with open(source, "rb") as fh:
            response = httpx.post(
                url,
                files={"file": (source.name, fh, "application/pdf")},
                data={"output_format": "html"},
                timeout=timeout,
            )
    except httpx.HTTPError as e:
        print(f"  Spark HTML request failed: {e}", file=sys.stderr)
        return

    if response.is_error:
        print(
            f"  Spark HTML returned HTTP {response.status_code}: {response.text[:300]}",
            file=sys.stderr,
        )
        return

    try:
        payload = response.json()
    except ValueError as e:
        print(f"  Spark HTML response was not JSON: {e}", file=sys.stderr)
        return

    if not payload.get("success"):
        print(
            f"  Spark HTML extraction failed: {payload.get('error', 'unknown error')}",
            file=sys.stderr,
        )
        return

    html = payload.get("output")
    if not html:
        print("  Spark HTML response missing 'output' field", file=sys.stderr)
        return

    (marker_output_dir / "document.html").write_text(html)
    (marker_output_dir / "html_metadata.json").write_text(
        json.dumps(payload.get("metadata", {}), indent=2)
    )

    for name, encoded in (payload.get("images") or {}).items():
        image_path = image_dir / Path(name).name
        try:
            image_path.write_bytes(base64.b64decode(encoded))
        except Exception as e:
            print(f"  Failed to write HTML image {name}: {e}", file=sys.stderr)
