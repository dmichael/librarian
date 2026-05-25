"""Extraction QA for comparing raw parser outputs.

The first baseline is intentionally simple: use the embedded PDF text layer
via pdftotext and compare numbered equations against Marker output. This is
not a replacement extractor; it is a disagreement detector for human review.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from librarian.equations import extract_equations_from_blocks
from librarian.extractors import pdftext
from librarian.files import marker_content_json, marker_dir


@dataclass
class NumberedEquation:
    number: str
    text: str
    line: int


@dataclass
class EquationComparison:
    number: str
    marker: str | None
    pdftext: str | None
    status: str
    notes: list[str]


@dataclass
class ExtractionQAResult:
    marker_equations: int
    pdftext_equations: int
    findings: int
    review_dir: str
    raw_outputs: dict[str, str]


def write_extraction_qa(source: Path, output_dir: Path) -> ExtractionQAResult:
    """Write extraction QA artifacts comparing Marker and pdftotext.

    Artifacts:
      - raw/pdftext/layout.txt
      - review/equation_diffs.json
      - review/extraction_qa.md
      - review/artifacts.json

    Raises if Marker JSON is missing or the pdftext extractor fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    review_dir = output_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    marker_json = marker_content_json(output_dir)
    if marker_json is None:
        raise FileNotFoundError(f"Marker JSON not found under {marker_dir(output_dir)}")

    pdftext.extract(source, output_dir)
    pdftext_path = output_dir / pdftext.ARTIFACT_REL_PATH

    comparisons = compare_marker_to_pdftext(marker_json, pdftext_path)
    findings = sum(1 for item in comparisons if item.status != "ok")

    (review_dir / "equation_diffs.json").write_text(
        json.dumps([asdict(item) for item in comparisons], indent=2)
    )

    result = ExtractionQAResult(
        marker_equations=sum(1 for item in comparisons if item.marker is not None),
        pdftext_equations=sum(1 for item in comparisons if item.pdftext is not None),
        findings=findings,
        review_dir=str(review_dir),
        raw_outputs={pdftext.NAME: str(pdftext.ARTIFACT_REL_PATH)},
    )
    _write_review_markdown(review_dir / "extraction_qa.md", comparisons, result)
    _write_artifact_manifest(output_dir, result)
    return result


def compare_marker_to_pdftext(
    marker_json_path: Path,
    pdftext_path: Path,
) -> list[EquationComparison]:
    """Compare numbered Marker equations to numbered pdftotext equations."""
    marker_equations = _marker_equations_by_number(marker_json_path)
    pdftext_equations = _pdftext_equations_by_number(pdftext_path.read_text(errors="replace"))

    numbers = sorted(
        set(marker_equations) | set(pdftext_equations),
        key=lambda value: int(value) if value.isdigit() else value,
    )
    comparisons = []
    for number in numbers:
        marker = marker_equations.get(number)
        pdftext = pdftext_equations.get(number)
        notes = []
        status = "ok"

        if marker is None:
            status = "missing_marker"
            notes.append("Numbered equation appears in pdftotext but not Marker JSON.")
        elif pdftext is None:
            status = "missing_pdftext"
            notes.append("Numbered equation appears in Marker JSON but not pdftotext.")
        elif _normalize(marker) != _normalize(pdftext):
            status = "review"
            notes.append("Marker and pdftotext disagree; see raw artifacts for LLM reconciliation.")

        comparisons.append(
            EquationComparison(
                number=number,
                marker=marker,
                pdftext=pdftext,
                status=status,
                notes=notes,
            )
        )

    return comparisons


def extract_numbered_equations_from_pdftext(text: str) -> list[NumberedEquation]:
    """Find equation-looking lines ending with a parenthesized number."""
    lines = text.splitlines()
    equations = []
    equation_number = re.compile(r"\((?P<number>\d{1,3})\)")

    for idx, line in enumerate(lines):
        for match in equation_number.finditer(line):
            body = line[: match.start()].strip()
            if not _looks_like_equation_line(body):
                continue

            number = match.group("number")
            context_lines = []
            if "=" not in body:
                for prev_idx in range(max(0, idx - 2), idx):
                    prev = lines[prev_idx].strip()
                    if _looks_like_equation_line(prev):
                        context_lines.append(prev)

            if body:
                context_lines.append(body)

            equations.append(
                NumberedEquation(
                    number=number,
                    text=" ".join(context_lines),
                    line=idx + 1,
                )
            )

    return equations


def _marker_equations_by_number(marker_json_path: Path) -> dict[str, str]:
    payload = json.loads(marker_json_path.read_text())
    blocks = payload.get("blocks", payload if isinstance(payload, list) else [])
    equations = extract_equations_from_blocks(blocks)
    by_number = {}
    for idx, equation in enumerate(equations, start=1):
        number = equation.equation_number or str(idx)
        by_number[number] = equation.latex
    return by_number


def _pdftext_equations_by_number(text: str) -> dict[str, str]:
    return {equation.number: equation.text for equation in extract_numbered_equations_from_pdftext(text)}


def _looks_like_equation_line(line: str) -> bool:
    if not line:
        return False
    if "=" in line:
        return True
    if len(line.split()) > 12:
        return False
    compact = re.sub(r"\s+", "", line)
    return bool(re.search(r"[A-Za-z][A-Za-z0-9]?=", compact))


def _normalize(value: str) -> str:
    """Whitespace-insensitive comparison key for equation strings."""
    return re.sub(r"\s+", "", value or "")


def _write_review_markdown(
    path: Path,
    comparisons: list[EquationComparison],
    result: ExtractionQAResult,
) -> None:
    lines = [
        "# Extraction QA",
        "",
        "## Summary",
        "",
        f"- Marker equations: {result.marker_equations}",
        f"- pdftotext equations: {result.pdftext_equations}",
        f"- Findings needing review: {result.findings}",
    ]
    for name, output_path in sorted(result.raw_outputs.items()):
        lines.append(f"- Raw `{name}` output: `{output_path}`")

    lines.extend(
        [
            "",
            "## Equation Comparison",
            "",
            "The `pdftotext` cells are raw embedded-PDF text. On older PDFs,",
            "custom symbolic fonts can appear as unrelated Unicode glyphs;",
            "treat them as comparison evidence, not cleaned text.",
            "",
            "| Eq. | Status | Marker | pdftotext | Notes |",
            "|---|---|---|---|---|",
        ]
    )

    for item in comparisons:
        lines.append(
            "| "
            + " | ".join(
                [
                    item.number,
                    item.status,
                    _cell(item.marker),
                    _cell(item.pdftext),
                    _cell("; ".join(item.notes)),
                ]
            )
            + " |"
        )

    path.write_text("\n".join(lines) + "\n")


def _write_artifact_manifest(output_dir: Path, result: ExtractionQAResult) -> None:
    """Write a compact manifest for artifact discovery and audits."""
    expected = {
        "marker_json": "raw/marker/document.json",
        "marker_markdown": "raw/marker/document.md",
        "marker_metadata": "raw/marker/metadata.json",
        "marker_html": "raw/marker/document.html",
        "marker_html_metadata": "raw/marker/html_metadata.json",
        "pdftext_layout": result.raw_outputs.get("pdftext"),
        "equation_diffs": "review/equation_diffs.json",
        "qa_report": "review/extraction_qa.md",
    }

    artifacts = []
    for role, rel_path in expected.items():
        if not rel_path:
            artifacts.append({"role": role, "path": None, "exists": False, "bytes": None})
            continue

        artifact_path = output_dir / rel_path
        artifacts.append(
            {
                "role": role,
                "path": rel_path,
                "exists": artifact_path.exists(),
                "bytes": artifact_path.stat().st_size if artifact_path.exists() else None,
            }
        )

    image_artifacts = []
    for image_path in sorted((output_dir / "raw" / "marker" / "images").glob("_page_*")):
        if image_path.is_file():
            image_artifacts.append(
                {
                    "path": str(image_path.relative_to(output_dir)),
                    "bytes": image_path.stat().st_size,
                }
            )

    manifest = {
        "book_id": output_dir.name,
        "base_dir": ".",
        "artifacts": artifacts,
        "images": image_artifacts,
        "qa": asdict(result),
    }
    (output_dir / "review" / "artifacts.json").write_text(json.dumps(manifest, indent=2))


def _cell(value: str | None, max_len: int = 180) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    value = value.replace("|", "\\|")
    if len(value) > max_len:
        value = value[: max_len - 1] + "..."
    return f"`{value}`"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write extraction QA artifacts for a PDF")
    parser.add_argument("source", type=Path, help="Source PDF")
    parser.add_argument("output_dir", type=Path, help="Existing converted/<book_id> directory")
    args = parser.parse_args()

    result = write_extraction_qa(args.source, args.output_dir)
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
