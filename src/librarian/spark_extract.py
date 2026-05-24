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
import sys
from pathlib import Path

import httpx


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

    Produces the same files as the local extract_pdf path:
      - {book_id}.json       chunks (block list)
      - {book_id}_meta.json  document metadata
      - {book_id}.md         markdown rendered by _chunks_to_markdown

    When write_html=True, also writes human-review artifacts from a second
    Marker pass:
      - {book_id}.html            rendered HTML
      - {book_id}_html_meta.json  HTML-pass metadata
      - referenced image files    JPEG/PNG payloads used by the HTML

    Args:
        source: Path to PDF file
        output_dir: Per-book output directory (its name is the book id)
        timeout: HTTP request timeout in seconds
        write_html: Also request Marker HTML output for human QA

    Returns:
        True on successful extraction and write, False on any failure.
    """
    book_id = output_dir.name
    url = f"{get_spark_url()}/marker/upload"

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

    # Write content + metadata to the predictable paths the rest of the
    # pipeline expects (matching what _collect_marker_output produces on
    # the local path).
    chunks_path = output_dir / f"{book_id}.json"
    chunks_path.write_text(json.dumps(chunks_data, indent=2))
    (output_dir / f"{book_id}_meta.json").write_text(
        json.dumps(payload.get("metadata", {}), indent=2)
    )

    # Also write the human-readable markdown rendering, matching what the
    # local + cloud paths produce. Imported here (not module-top) to keep
    # spark_extract self-contained for callers that only want the chunks
    # write. The leading underscore on _chunks_to_markdown is intentional
    # private-within-package — we're a sibling, not an external user.
    from librarian.extract import _chunks_to_markdown
    (output_dir / f"{book_id}.md").write_text(_chunks_to_markdown(chunks_path))

    if write_html:
        _write_html_artifacts(source, output_dir, timeout)

    _write_qa_artifacts(source, output_dir)

    return True


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
    book_id = output_dir.name
    url = f"{get_spark_url()}/marker/upload"

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

    (output_dir / f"{book_id}.html").write_text(html)
    (output_dir / f"{book_id}_html_meta.json").write_text(
        json.dumps(payload.get("metadata", {}), indent=2)
    )

    for name, encoded in (payload.get("images") or {}).items():
        image_path = output_dir / Path(name).name
        try:
            image_path.write_bytes(base64.b64decode(encoded))
        except Exception as e:
            print(f"  Failed to write HTML image {name}: {e}", file=sys.stderr)
