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
from librarian.extractors import ExtractorResult, PdfTextExtractor
from librarian.files import find_content_json, marker_dir


# Some old PDFs use custom symbolic fonts. pdftotext can decode those glyphs as
# unrelated Unicode codepoints, including CJK punctuation/characters. Keep the
# observed artifacts named here so the equation-line heuristic is explainable.
PDFTEXT_MOJIBAKE_EQUALS = "\u82f7"
PDFTEXT_MOJIBAKE_MATH_MARKERS = set(
    "=+-/^()[]{}"
    + PDFTEXT_MOJIBAKE_EQUALS
    + "\u5173\u517e\u5171\u5172\u1828\u2d31\u2bdd"
)


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
    success: bool
    marker_equations: int
    pdftext_equations: int
    findings: int
    review_dir: str
    raw_outputs: dict[str, str | None]
    error: str | None = None


def write_extraction_qa(source: Path, output_dir: Path) -> ExtractionQAResult:
    """Write baseline extraction QA artifacts for a converted PDF.

    Artifacts:
      - raw/pdftext/layout.txt
      - review/equation_diffs.json
      - review/extraction_qa.md

    This function is best-effort by design. It returns a result object instead
    of raising so raw extraction can still produce provenance artifacts even
    when a QA backend fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    review_dir = output_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    marker_json = find_content_json(output_dir)
    if marker_json is None:
        result = ExtractionQAResult(
            success=False,
            marker_equations=0,
            pdftext_equations=0,
            findings=0,
            review_dir=str(review_dir),
            raw_outputs={},
            error=f"Marker JSON not found under {marker_dir(output_dir)}",
        )
        _write_review_markdown(review_dir / "extraction_qa.md", [], result)
        return result

    pdftext = write_pdftext_baseline(source, output_dir)
    if not pdftext.success or not pdftext.output_path:
        result = ExtractionQAResult(
            success=False,
            marker_equations=_count_marker_equations(marker_json),
            pdftext_equations=0,
            findings=0,
            review_dir=str(review_dir),
            raw_outputs={pdftext.name: _artifact_ref(pdftext.output_path, output_dir)},
            error=pdftext.error,
        )
        _write_review_markdown(review_dir / "extraction_qa.md", [], result)
        return result

    comparisons = compare_marker_to_pdftext(marker_json, Path(pdftext.output_path))
    findings = sum(1 for item in comparisons if item.status != "ok")

    (review_dir / "equation_diffs.json").write_text(
        json.dumps([asdict(item) for item in comparisons], indent=2)
    )

    result = ExtractionQAResult(
        success=True,
        marker_equations=sum(1 for item in comparisons if item.marker is not None),
        pdftext_equations=sum(1 for item in comparisons if item.pdftext is not None),
        findings=findings,
        review_dir=str(review_dir),
        raw_outputs={pdftext.name: _artifact_ref(pdftext.output_path, output_dir)},
    )
    _write_review_markdown(review_dir / "extraction_qa.md", comparisons, result)
    _write_artifact_manifest(output_dir, result)
    return result


def write_pdftext_baseline(source: Path, output_dir: Path) -> ExtractorResult:
    """Extract embedded PDF text with pdftotext -layout."""
    return PdfTextExtractor().run(source, output_dir)


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
        else:
            notes.extend(_symbol_disagreement_notes(marker, pdftext))
            if notes:
                status = "review"

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
        if _is_pdftext_noise_line(line):
            continue

        for match in equation_number.finditer(line):
            body = line[: match.start()].strip()
            if not _looks_like_equation_line(body):
                continue

            number = match.group("number")
            context_lines = []
            if "=" not in body and PDFTEXT_MOJIBAKE_EQUALS not in body:
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


def _count_marker_equations(marker_json_path: Path) -> int:
    return len(_marker_equations_by_number(marker_json_path))


def _pdftext_equations_by_number(text: str) -> dict[str, str]:
    return {equation.number: equation.text for equation in extract_numbered_equations_from_pdftext(text)}


def _looks_like_equation_line(line: str) -> bool:
    if not line:
        return False
    if "=" in line or PDFTEXT_MOJIBAKE_EQUALS in line:
        return True

    if len(line.split()) > 12:
        return False

    marker_count = sum(1 for char in line if char in PDFTEXT_MOJIBAKE_MATH_MARKERS)
    if marker_count >= 2:
        return True

    compact = re.sub(r"\s+", "", line)
    return bool(
        re.search(
            rf"[A-Za-z][A-Za-z0-9]?[={PDFTEXT_MOJIBAKE_EQUALS}]",
            compact,
        )
    )


def _is_pdftext_noise_line(line: str) -> bool:
    noise_markers = [
        "0031-9007",
        "PhysRevLett",
        "PACS numbers",
        "The American Physical Society",
    ]
    return any(marker in line for marker in noise_markers)


def _symbol_disagreement_notes(marker: str, pdftext: str) -> list[str]:
    """Detect high-signal symbol disagreements without pretending full equivalence."""
    notes = []
    marker_phase = set(re.findall(r"\\phi_\{?([A-Za-z0-9]+)\}?", marker))
    pdftext_phase = set(re.findall(r"\bf([A-Za-z0-9])\b", pdftext))

    if marker_phase and pdftext_phase:
        marker_symbols = {f"phi_{value}" for value in marker_phase}
        pdftext_symbols = {f"phi_{value}" for value in pdftext_phase}
        if marker_symbols != pdftext_symbols:
            notes.append(
                "Possible phase-symbol disagreement: "
                f"Marker has {sorted(marker_symbols)}, "
                f"pdftotext suggests {sorted(pdftext_symbols)}."
            )

    return notes


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
        f"- Success: {result.success}",
        f"- Marker equations: {result.marker_equations}",
        f"- pdftotext equations: {result.pdftext_equations}",
        f"- Findings needing review: {result.findings}",
    ]
    for name, output_path in sorted(result.raw_outputs.items()):
        if output_path:
            lines.append(f"- Raw `{name}` output: `{output_path}`")
    if result.error:
        lines.append(f"- Error: {result.error}")

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


def _artifact_ref(path: str | None, output_dir: Path) -> str | None:
    if not path:
        return None
    artifact_path = Path(path)
    try:
        return str(artifact_path.relative_to(output_dir))
    except ValueError:
        return str(artifact_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write extraction QA artifacts for a PDF")
    parser.add_argument("source", type=Path, help="Source PDF")
    parser.add_argument("output_dir", type=Path, help="Existing converted/<book_id> directory")
    args = parser.parse_args()

    result = write_extraction_qa(args.source, args.output_dir)
    print(json.dumps(asdict(result), indent=2))
    raise SystemExit(0 if result.success else 1)


if __name__ == "__main__":
    main()
