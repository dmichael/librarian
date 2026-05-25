"""LLM reconciliation for the equations domain.

This module reconciles disagreements between Marker (LaTeX form) and pdftotext
(raw glyphs) for numbered equations. It is *not* a general reconciler — other
domains use pick-a-winner logic and do not need an LLM. See
specs/multi-extractor-domains/requirements.md for the broader design.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from librarian.files import marker_markdown


DEFAULT_MODEL = "qwen3:14b"


RECONCILIATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["patches"],
    "properties": {
        "patches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "target_type",
                    "target_id",
                    "operation",
                    "before",
                    "after",
                    "confidence",
                    "rationale",
                    "evidence",
                ],
                "properties": {
                    "target_type": {"type": "string", "enum": ["equation"]},
                    "target_id": {"type": "string"},
                    "operation": {"type": "string", "enum": ["replace"]},
                    "before": {"type": "string"},
                    "after": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "rationale": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


class ReconciliationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: str
    target_id: str
    operation: str
    before: str
    after: str
    confidence: str
    rationale: str
    evidence: list[str]


class ReconciliationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patches: list[ReconciliationPatch] = Field(default_factory=list)


class AppliedPatch(ReconciliationPatch):
    applied: bool
    reason: str | None = None


def build_reconciliation_packet(book_dir: Path) -> dict[str, Any]:
    """Build a compact evidence packet of contested equations for the LLM."""
    equation_diffs = _load_json(book_dir / "review" / "equation_diffs.json", default=[])
    findings = [item for item in equation_diffs if item.get("status") != "ok"]

    marker_md = marker_markdown(book_dir)
    marker_text = marker_md.read_text(errors="replace") if marker_md else ""
    for item in findings:
        number = str(item.get("number", ""))
        if number:
            item["raw_markdown_candidates"] = _raw_equation_candidates(marker_text, number)

    return {
        "domain": "equations",
        "book_dir": str(book_dir),
        "marker_markdown_path": "raw/marker/document.md",
        "evidence_paths": {
            "equation_diffs": "review/equation_diffs.json",
            "extraction_qa": "review/extraction_qa.md",
        },
        "output_paths": {
            "patched_markdown": "clean/document.md",
            "corrections": "clean/corrections.json",
        },
        "findings": findings,
    }


def reconciliation_prompt(packet: dict[str, Any]) -> list[dict[str, str]]:
    """Create chat messages for a contested-equation reconciliation pass."""
    system = (
        "You reconcile contested numbered equations extracted from a PDF by two "
        "different tools: Marker (LaTeX form) and pdftotext (raw glyphs). "
        "Use only the supplied evidence. Do not invent content. "
        "Return JSON only. Prefer no patch over a speculative patch."
    )
    user = (
        "Review the contested equations and propose deterministic patches that fix the "
        "Marker LaTeX where pdftotext's raw glyphs make the correct symbol unambiguous. "
        "Each patch must replace exact Markdown text from the raw Marker output with "
        "corrected text. If the evidence is insufficient, return an empty patches array.\n\n"
        f"Evidence packet:\n{json.dumps(packet, indent=2)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call_openai_compatible_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
    json_mode: str,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat-completions endpoint and parse JSON content."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "stream": False,
    }
    if json_mode == "schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "document_reconciliation",
                "schema": RECONCILIATION_SCHEMA,
                "strict": True,
            },
        }
    elif json_mode == "object":
        payload["response_format"] = {"type": "json_object"}
        messages[-1]["content"] += "\n\nReturn a JSON object matching this schema:\n"
        messages[-1]["content"] += json.dumps(RECONCILIATION_SCHEMA, indent=2)
    elif json_mode != "none":
        raise ValueError(f"Unsupported json mode: {json_mode}")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return ReconciliationProposal.model_validate(_parse_json_object(content)).model_dump()


def write_reconciliation_proposal(book_dir: Path, proposal: dict[str, Any]) -> Path:
    review_dir = book_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / "reconciliation_proposed.json"
    path.write_text(json.dumps(proposal, indent=2) + "\n")
    return path


def apply_reconciliation(book_dir: Path, proposal: dict[str, Any]) -> list[AppliedPatch]:
    """Apply proposed replace patches into clean/document.md and record corrections."""
    source = marker_markdown(book_dir)
    if source is None:
        raise FileNotFoundError(f"Marker Markdown not found under {book_dir / 'raw' / 'marker'}")

    clean_dir = book_dir / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    document_path = clean_dir / "document.md"

    content = source.read_text()
    applied: list[AppliedPatch] = []
    validated = ReconciliationProposal.model_validate(proposal)
    for patch in validated.patches:
        item = AppliedPatch(**patch.model_dump(), applied=False)
        if patch.operation != "replace":
            item.reason = f"Unsupported operation: {patch.operation}"
        elif not patch.before:
            item.reason = "Patch has empty before text."
        elif patch.before not in content:
            item.reason = "Before text not found in raw Markdown."
        else:
            content = content.replace(patch.before, patch.after, 1)
            item.applied = True
        applied.append(item)

    document_path.write_text(content)
    corrections_path = clean_dir / "corrections.json"
    corrections_path.write_text(
        json.dumps([item.model_dump() for item in applied], indent=2) + "\n"
    )
    return applied


def reconcile_book(
    book_dir: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    json_mode: str,
    apply: bool,
    packet_only: bool,
) -> dict[str, Any]:
    packet = build_reconciliation_packet(book_dir)
    if packet_only:
        path = book_dir / "review" / "reconciliation_packet.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(packet, indent=2) + "\n")
        return {"packet": str(path), "proposal": None, "applied": []}

    proposal = call_openai_compatible_chat(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=reconciliation_prompt(packet),
        timeout=timeout,
        json_mode=json_mode,
    )
    proposal_path = write_reconciliation_proposal(book_dir, proposal)
    applied = apply_reconciliation(book_dir, proposal) if apply else []
    return {
        "packet": None,
        "proposal": str(proposal_path),
        "patches": len(proposal.get("patches", [])),
        "applied": [item.model_dump() for item in applied],
    }


def resolve_base_url(explicit: str | None) -> str:
    """Resolve the OpenAI-compatible /v1 base URL."""
    if explicit:
        return explicit
    if base_url := os.getenv("OPENAI_BASE_URL"):
        return base_url
    raise ValueError(
        "Set OPENAI_BASE_URL to an OpenAI-compatible /v1 endpoint "
        "(for Spark Ollama: http://spark-f80b.local:11434/v1)."
    )


def _load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _raw_equation_candidates(markdown: str, number: str) -> list[str]:
    tag = rf"\tag{{{number}}}"
    paren = f"({number})"
    candidates = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if tag in stripped or stripped.endswith(paren):
            candidates.append(stripped)
    return candidates[:5]


def _parse_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            raise
        parsed = json.loads(content[start:end])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not a JSON object")
    parsed.setdefault("patches", [])
    if not isinstance(parsed["patches"], list):
        raise ValueError("LLM response field 'patches' was not a list")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile contested equations with an LLM")
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
