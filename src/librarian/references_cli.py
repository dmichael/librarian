"""CLI for bibliography extraction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from librarian.references import extract_references_with_grobid, resolve_grobid_base_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract document references to CSL-JSON")
    parser.add_argument("source_pdf", type=Path, help="Source PDF")
    parser.add_argument("book_dir", type=Path, help="converted/<book_id> directory")
    parser.add_argument(
        "--grobid-url",
        default=None,
        help="GROBID service root URL. Defaults to GROBID_BASE_URL.",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--consolidate-citations",
        choices=["0", "1", "2"],
        default="0",
        help="GROBID citation consolidation mode.",
    )
    args = parser.parse_args()

    try:
        result = extract_references_with_grobid(
            args.source_pdf,
            args.book_dir,
            grobid_base_url=resolve_grobid_base_url(args.grobid_url),
            timeout=args.timeout,
            consolidate_citations=args.consolidate_citations,
        )
    except Exception as exc:
        print(f"reference extraction failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()

