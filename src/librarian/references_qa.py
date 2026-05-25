"""QA checks for bibliography extraction artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class VisibleReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: int
    text: str
    source_block: int


class StructuredReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: int
    id: str
    title: str | None = None
    raw_reference: str | None = None


class ReferenceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str
    message: str
    labels: list[int] = []


class ReferencesQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker_json_path: str
    csl_json_path: str
    marker_count: int
    csl_count: int
    marker_labels: list[int]
    csl_labels: list[int]
    missing_marker_labels: list[int]
    extra_structured_labels: list[int]
    duplicate_marker_labels: list[int]
    likely_split_labels: list[int]
    issues: list[ReferenceIssue]


def write_references_qa(book_dir: Path) -> ReferencesQAResult:
    """Compare visible Marker bibliography entries with clean CSL-JSON."""
    result = build_references_qa(book_dir)
    review_dir = book_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "references_qa.json").write_text(
        json.dumps(result.model_dump(), indent=2) + "\n"
    )
    (review_dir / "references_qa.md").write_text(_render_markdown(result))
    return result


def build_references_qa(book_dir: Path) -> ReferencesQAResult:
    marker_json_path = book_dir / "raw" / "marker" / "document.json"
    csl_json_path = book_dir / "clean" / "references.csl.json"
    if not marker_json_path.exists():
        raise FileNotFoundError(marker_json_path)
    if not csl_json_path.exists():
        raise FileNotFoundError(csl_json_path)

    visible = extract_visible_references_from_marker_json(marker_json_path)
    structured = extract_structured_references_from_csl_json(csl_json_path)

    marker_labels = [item.label for item in visible]
    csl_labels = [item.label for item in structured]
    missing_marker_labels = _missing_in_sequence(marker_labels)
    extra_structured_labels = sorted(set(csl_labels) - set(marker_labels))
    duplicate_marker_labels = _duplicates(marker_labels)
    likely_split_labels = _likely_split_labels(visible, structured)
    issues = _reference_issues(
        marker_count=len(visible),
        csl_count=len(structured),
        missing_marker_labels=missing_marker_labels,
        extra_structured_labels=extra_structured_labels,
        duplicate_marker_labels=duplicate_marker_labels,
        likely_split_labels=likely_split_labels,
    )

    return ReferencesQAResult(
        marker_json_path=str(marker_json_path),
        csl_json_path=str(csl_json_path),
        marker_count=len(visible),
        csl_count=len(structured),
        marker_labels=marker_labels,
        csl_labels=csl_labels,
        missing_marker_labels=missing_marker_labels,
        extra_structured_labels=extra_structured_labels,
        duplicate_marker_labels=duplicate_marker_labels,
        likely_split_labels=likely_split_labels,
        issues=issues,
    )


def extract_visible_references_from_marker_json(path: Path) -> list[VisibleReference]:
    """Extract numbered bibliography entries from Marker block HTML."""
    data = json.loads(path.read_text())
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError(f"{path} does not contain Marker blocks")

    references: list[VisibleReference] = []
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        html = block.get("html")
        if not isinstance(html, str):
            continue
        for item in _list_items(html):
            if ref := _visible_reference(item, block_index):
                references.append(ref)
    return references


def extract_structured_references_from_csl_json(path: Path) -> list[StructuredReference]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a CSL-JSON list")

    references: list[StructuredReference] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or f"ref-{index}")
        references.append(
            StructuredReference(
                label=_label_from_id(item_id) or index,
                id=item_id,
                title=_string_or_none(item.get("title")),
                raw_reference=_raw_note(item),
            )
        )
    return references


def _reference_issues(
    *,
    marker_count: int,
    csl_count: int,
    missing_marker_labels: list[int],
    extra_structured_labels: list[int],
    duplicate_marker_labels: list[int],
    likely_split_labels: list[int],
) -> list[ReferenceIssue]:
    issues: list[ReferenceIssue] = []
    if marker_count != csl_count:
        issues.append(
            ReferenceIssue(
                code="reference_count_mismatch",
                severity="warn",
                message=(
                    f"Marker-visible bibliography has {marker_count} numbered entries; "
                    f"CSL-JSON has {csl_count} structured entries."
                ),
            )
        )
    if missing_marker_labels:
        issues.append(
            ReferenceIssue(
                code="missing_marker_labels",
                severity="warn",
                message="Marker-visible bibliography labels are not sequential.",
                labels=missing_marker_labels,
            )
        )
    if extra_structured_labels:
        issues.append(
            ReferenceIssue(
                code="extra_structured_labels",
                severity="warn",
                message="Structured references contain labels not visible in Marker bibliography.",
                labels=extra_structured_labels,
            )
        )
    if duplicate_marker_labels:
        issues.append(
            ReferenceIssue(
                code="duplicate_marker_labels",
                severity="warn",
                message="Marker-visible bibliography contains duplicate labels.",
                labels=duplicate_marker_labels,
            )
        )
    if likely_split_labels:
        issues.append(
            ReferenceIssue(
                code="likely_split_reference",
                severity="warn",
                message=(
                    "Structured references appear to split content that Marker keeps inside one "
                    "numbered bibliography entry."
                ),
                labels=likely_split_labels,
            )
        )
    return issues


def _render_markdown(result: ReferencesQAResult) -> str:
    lines = [
        "# References QA",
        "",
        f"- Marker-visible numbered references: {result.marker_count}",
        f"- CSL-JSON structured references: {result.csl_count}",
        f"- Marker labels: {_label_range(result.marker_labels)}",
        f"- CSL labels: {_label_range(result.csl_labels)}",
        "",
        "## Issues",
        "",
    ]
    if not result.issues:
        lines.append("- None detected.")
    else:
        for issue in result.issues:
            labels = f" Labels: {issue.labels}." if issue.labels else ""
            lines.append(f"- `{issue.code}` ({issue.severity}): {issue.message}{labels}")
    lines.append("")
    return "\n".join(lines)


def _list_items(html: str) -> list[str]:
    parser = _ListItemParser()
    parser.feed(html)
    return parser.items


def _visible_reference(text: str, block_index: int) -> VisibleReference | None:
    normalized = " ".join(text.split())
    match = re.match(r"^\[(\d+)\]\s+(.+)$", normalized)
    if not match:
        return None
    return VisibleReference(
        label=int(match.group(1)),
        text=match.group(2).strip(),
        source_block=block_index,
    )


def _label_from_id(item_id: str) -> int | None:
    match = re.search(r"(\d+)$", item_id)
    return int(match.group(1)) if match else None


def _raw_note(item: dict[str, Any]) -> str | None:
    note = _string_or_none(item.get("note"))
    if note and note.startswith("Raw reference:"):
        return note.removeprefix("Raw reference:").strip()
    return note


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _missing_in_sequence(labels: list[int]) -> list[int]:
    if not labels:
        return []
    present = set(labels)
    return [label for label in range(min(labels), max(labels) + 1) if label not in present]


def _duplicates(labels: list[int]) -> list[int]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for label in labels:
        if label in seen:
            duplicates.add(label)
        seen.add(label)
    return sorted(duplicates)


def _likely_split_labels(
    visible: list[VisibleReference], structured: list[StructuredReference]
) -> list[int]:
    visible_by_label = {item.label: item for item in visible}
    likely: set[int] = set()
    for item in structured:
        if item.label in visible_by_label:
            continue
        raw = item.raw_reference or item.title or ""
        if not raw:
            continue
        previous = visible_by_label.get(item.label - 1)
        if previous and _compact(raw[:80]) in _compact(previous.text):
            likely.add(previous.label)
    return sorted(likely)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _label_range(labels: list[int]) -> str:
    if not labels:
        return "none"
    return f"{min(labels)}-{max(labels)} ({len(labels)} labels)"


class _ListItemParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[str] = []
        self._stack: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "li":
            self._stack.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "li" and self._stack:
            text = "".join(self._stack.pop()).strip()
            if text:
                self.items.append(text)

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1].append(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write bibliography QA artifacts")
    parser.add_argument("book_dir", type=Path, help="converted/<book_id> directory")
    args = parser.parse_args()

    try:
        result = write_references_qa(args.book_dir)
    except Exception as exc:
        print(f"references QA failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()
