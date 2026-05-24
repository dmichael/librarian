"""CLI for LLM reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from librarian.reconcile import DEFAULT_MODEL, reconcile_book, resolve_base_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile extraction QA findings with an LLM")
    parser.add_argument("book_dir", type=Path, help="converted/<book_id> directory")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible /v1 endpoint. Defaults to OPENAI_BASE_URL.",
    )
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", "ollama"))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--json-mode",
        choices=["object", "schema", "none"],
        default=os.getenv("LIBRARIAN_RECONCILE_JSON_MODE", "object"),
        help="Use object for broad Ollama compatibility; schema for stricter OpenAI-compatible servers.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write clean/document.md and clean/corrections.json",
    )
    parser.add_argument(
        "--packet-only",
        action="store_true",
        help="Only write review/reconciliation_packet.json; do not call the LLM.",
    )
    args = parser.parse_args()

    try:
        result = reconcile_book(
            args.book_dir,
            base_url="" if args.packet_only else resolve_base_url(args.base_url),
            api_key=args.api_key,
            model=args.model,
            timeout=args.timeout,
            json_mode=args.json_mode,
            apply=args.apply,
            packet_only=args.packet_only,
        )
    except Exception as exc:
        print(f"reconciliation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
