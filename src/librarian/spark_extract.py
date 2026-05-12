"""Extract PDFs via the marker HTTP service running on the DGX Spark.

This module is the local-network equivalent of cloud_extract.py: instead
of dispatching to Modal A100s, it POSTs each PDF to the Spark's
marker-service container at agents.local-discoverable
`http://spark-f80b.local:8001/marker/upload` and writes the response to
disk in the same layout the rest of the pipeline expects.

Override the host via the `LIBRARIAN_SPARK_URL` env var if the Spark
moves or you point this at a different host.
"""

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
) -> bool:
    """POST a PDF to the Spark marker service and write chunks to output_dir.

    Produces the same files as the local extract_pdf path:
      - {book_id}.json       chunks (block list)
      - {book_id}_meta.json  document metadata
      - {book_id}.md         markdown rendered by _chunks_to_markdown

    Args:
        source: Path to PDF file
        output_dir: Per-book output directory (its name is the book id)
        timeout: HTTP request timeout in seconds

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
    (output_dir / f"{book_id}.json").write_text(
        json.dumps(chunks_data, indent=2)
    )
    (output_dir / f"{book_id}_meta.json").write_text(
        json.dumps(payload.get("metadata", {}), indent=2)
    )

    return True
