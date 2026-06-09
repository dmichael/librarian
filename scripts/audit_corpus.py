"""Comprehensive audit of extracted artifacts.

For each book directory, walks every artifact and reports on:

  Marker side (raw/marker/):
    - presence of document.json, document.md, document.html, metadata.json,
      images directory
    - block count and types
    - for every Equation block: was it parsed into equations.json?
      did number extraction succeed?
      is the latex string non-empty?
    - for every Figure / Table / Code block found in marker (these are
      candidate future domains): count them

  GROBID side (raw/grobid/):
    - presence of references.tei.xml and references.csl.json
    - count of <biblStruct> elements in TEI vs records in CSL — equal?
    - per-record completeness: does each have title? authors? year?
    - DOIs present in TEI but missing from CSL?

  Cross-check:
    - equations.json count vs marker Equation-block count
    - any equation block with no parsed output → flagged

Output: per-book section, then a global summary at the end.

Usage: python scripts/audit_corpus.py [book_ids...]
       (no args = audit every book directory under output_path)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

# Allow running as a standalone script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from librarian.config import expand_path, load_config

OUTPUT_ROOT = expand_path(load_config()["output_path"])
TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def main() -> None:
    target_ids = [int(arg) for arg in sys.argv[1:]] if len(sys.argv) > 1 else None
    book_dirs = sorted(
        (d for d in OUTPUT_ROOT.iterdir() if d.is_dir() and d.name.isdigit()),
        key=lambda d: int(d.name),
    )
    if target_ids:
        book_dirs = [d for d in book_dirs if int(d.name) in target_ids]

    global_issues: list[str] = []
    totals = {
        "books": 0,
        "marker_equation_blocks": 0,
        "equations_parsed": 0,
        "equations_with_numbers": 0,
        "equations_missing_numbers": 0,
        "equations_no_latex": 0,
        "marker_figure_blocks": 0,
        "marker_table_blocks": 0,
        "marker_code_blocks": 0,
        "references_csl": 0,
        "references_tei": 0,
        "references_missing_title": 0,
        "references_missing_authors": 0,
        "references_missing_year": 0,
        "references_with_doi": 0,
        "citations": 0,
        "citations_linked": 0,
        "grobid_sections": 0,
        "grobid_figures": 0,
    }

    for book_dir in book_dirs:
        totals["books"] += 1
        issues = audit_book(book_dir, totals)
        if issues:
            global_issues.extend(f"[book {book_dir.name}] {i}" for i in issues)

    print("\n" + "=" * 72)
    print("GLOBAL SUMMARY")
    print("=" * 72)
    print(f"Books audited:                       {totals['books']}")
    print()
    print("MARKER:")
    print(f"  Equation blocks (raw):             {totals['marker_equation_blocks']}")
    print(f"  Equations parsed into equations.json: {totals['equations_parsed']}")
    print(f"    with numbers:                    {totals['equations_with_numbers']}")
    print(f"    missing numbers:                 {totals['equations_missing_numbers']}")
    print(f"    parser dropped (no latex):       {totals['equations_no_latex']}")
    print(f"  Figure blocks (future domain):     {totals['marker_figure_blocks']}")
    print(f"  Table blocks (future domain):      {totals['marker_table_blocks']}")
    print(f"  Code blocks (future domain):       {totals['marker_code_blocks']}")
    print()
    print("GROBID:")
    print(f"  CSL records (raw):                 {totals['references_csl']}")
    print(f"  TEI biblStruct count:              {totals['references_tei']}")
    print(f"  Records missing title:             {totals['references_missing_title']}")
    print(f"  Records missing authors:           {totals['references_missing_authors']}")
    print(f"  Records missing year:              {totals['references_missing_year']}")
    print(f"  Records with DOI:                  {totals['references_with_doi']}")
    print()
    print("GROBID FULLTEXT:")
    print(f"  Citations (in-text):               {totals['citations']}")
    print(f"    linked to bibliography:          {totals['citations_linked']}")
    print(f"  Sections:                          {totals['grobid_sections']}")
    print(f"  Figures/tables:                    {totals['grobid_figures']}")
    print()
    if global_issues:
        print(f"ISSUES ({len(global_issues)}):")
        for issue in global_issues:
            print(f"  - {issue}")
    else:
        print("No issues detected.")


def audit_book(book_dir: Path, totals: dict) -> list[str]:
    issues: list[str] = []
    print("\n" + "-" * 72)
    print(f"BOOK {book_dir.name}: {book_dir}")
    print("-" * 72)

    issues.extend(_audit_marker(book_dir, totals))
    issues.extend(_audit_grobid(book_dir, totals))
    return issues


def _audit_marker(book_dir: Path, totals: dict) -> list[str]:
    issues: list[str] = []
    raw_marker = book_dir / "raw" / "marker"

    expected_files = ["document.json", "document.md", "document.html", "metadata.json", "equations.json"]
    for name in expected_files:
        path = raw_marker / name
        if not path.exists():
            issues.append(f"missing raw/marker/{name}")

    document_json = raw_marker / "document.json"
    if not document_json.exists():
        issues.append("no document.json — skipping marker block audit")
        return issues

    try:
        blocks = json.loads(document_json.read_text()).get("blocks", [])
    except json.JSONDecodeError as e:
        issues.append(f"document.json malformed: {e}")
        return issues

    block_type_counts: dict[str, int] = {}
    equation_blocks: list[tuple[int, dict]] = []
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("block_type", "")).strip()
        block_type_counts[block_type] = block_type_counts.get(block_type, 0) + 1
        if block_type.lower() == "equation":
            equation_blocks.append((i, block))

    print(f"  marker blocks: {len(blocks)} total")
    for bt, count in sorted(block_type_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {bt}: {count}")

    totals["marker_equation_blocks"] += len(equation_blocks)
    totals["marker_figure_blocks"] += block_type_counts.get("Figure", 0) + block_type_counts.get("FigureGroup", 0)
    totals["marker_table_blocks"] += block_type_counts.get("Table", 0)
    totals["marker_code_blocks"] += block_type_counts.get("Code", 0)

    # Audit equations.json against equation blocks
    equations_json = raw_marker / "equations.json"
    if equations_json.exists():
        try:
            parsed = json.loads(equations_json.read_text())
        except json.JSONDecodeError as e:
            issues.append(f"equations.json malformed: {e}")
            parsed = []
    else:
        parsed = []

    totals["equations_parsed"] += len(parsed)

    # Index parsed equations by block_index for cross-check
    parsed_by_block_index: dict[int, dict] = {}
    for eq in parsed:
        if "block_index" in eq:
            parsed_by_block_index[eq["block_index"]] = eq

    numbered = sum(1 for eq in parsed if eq.get("number"))
    missing_number = sum(1 for eq in parsed if not eq.get("number"))
    totals["equations_with_numbers"] += numbered
    totals["equations_missing_numbers"] += missing_number

    print(f"  equations: {len(equation_blocks)} raw blocks → {len(parsed)} parsed "
          f"({numbered} numbered, {missing_number} unnumbered)")

    # For each raw equation block, verify it was parsed
    for block_index, block in equation_blocks:
        if block_index not in parsed_by_block_index:
            html = (block.get("html") or "").strip()[:120]
            issues.append(f"equation at block {block_index} dropped by parser; html={html!r}")
            totals["equations_no_latex"] += 1
            continue
        eq = parsed_by_block_index[block_index]
        # Sanity: latex non-empty
        if not eq.get("latex", "").strip():
            issues.append(f"equation at block {block_index} parsed with empty latex")
        # Sanity: if html clearly contains a number tag but parser missed it, flag
        html = block.get("html") or ""
        if not eq.get("number"):
            visible_number = _visible_number_hint(html)
            if visible_number:
                issues.append(
                    f"equation at block {block_index}: html shows number ({visible_number!r}) "
                    f"but parser produced no number; html={html[:120]!r}"
                )

    return issues


def _visible_number_hint(html: str) -> str | None:
    """Detect a number-looking pattern in marker HTML for diagnostic purposes."""
    for pattern in (r"\\tag\{([^}]+)\}", r"\(([0-9]+[a-z]?)\)\s*</math>", r"</math>\s*\(([0-9]+[a-z]?)\)"):
        if match := re.search(pattern, html):
            return match.group(1)
    return None


def _audit_grobid(book_dir: Path, totals: dict) -> list[str]:
    issues: list[str] = []
    raw_grobid = book_dir / "raw" / "grobid"

    tei_path = raw_grobid / "references.tei.xml"
    csl_path = raw_grobid / "references.csl.json"

    if not tei_path.exists():
        issues.append("missing raw/grobid/references.tei.xml")
        return issues
    if not csl_path.exists():
        issues.append("missing raw/grobid/references.csl.json")
        return issues

    # Count <biblStruct> in TEI
    try:
        root = ET.fromstring(tei_path.read_text())
    except ET.ParseError as e:
        issues.append(f"references.tei.xml malformed: {e}")
        return issues
    tei_count = len(root.findall(".//tei:biblStruct", TEI_NS))

    try:
        csl = json.loads(csl_path.read_text())
    except json.JSONDecodeError as e:
        issues.append(f"references.csl.json malformed: {e}")
        return issues

    totals["references_tei"] += tei_count
    totals["references_csl"] += len(csl)

    print(f"  references: {tei_count} TEI biblStructs → {len(csl)} CSL records")
    if tei_count != len(csl):
        issues.append(f"reference count mismatch: TEI={tei_count}, CSL={len(csl)}")

    # Per-record completeness
    missing_title = missing_authors = missing_year = with_doi = 0
    for i, ref in enumerate(csl, start=1):
        if not ref.get("title") and not ref.get("note"):
            missing_title += 1
            issues.append(f"reference {ref.get('id', f'#{i}')}: no title and no raw note")
        if not ref.get("author"):
            missing_authors += 1
        if not ref.get("issued"):
            missing_year += 1
        if ref.get("DOI"):
            with_doi += 1

        # Cross-check: DOI in raw TEI but not in CSL record
        if not ref.get("DOI"):
            xml_id_match = re.search(r"(\d+)$", ref.get("id", "") or "")
            if xml_id_match:
                tei_index = int(xml_id_match.group(1)) - 1
                bibl_structs = root.findall(".//tei:biblStruct", TEI_NS)
                if 0 <= tei_index < len(bibl_structs):
                    bibl = bibl_structs[tei_index]
                    doi_node = next(
                        (n for n in bibl.findall(".//tei:idno", TEI_NS)
                         if (n.get("type") or "").upper() == "DOI"),
                        None,
                    )
                    if doi_node is not None and (doi_node.text or "").strip():
                        issues.append(
                            f"reference {ref['id']}: TEI has DOI={doi_node.text!r} "
                            "but CSL record is missing it"
                        )

    totals["references_missing_title"] += missing_title
    totals["references_missing_authors"] += missing_authors
    totals["references_missing_year"] += missing_year
    totals["references_with_doi"] += with_doi

    print(f"    completeness: title?{len(csl) - missing_title}/{len(csl)}  "
          f"authors?{len(csl) - missing_authors}/{len(csl)}  "
          f"year?{len(csl) - missing_year}/{len(csl)}  "
          f"with-DOI={with_doi}")

    # Fulltext artifacts (citations, sections, figures)
    for name in ("citations.json", "sections.json", "figures.json"):
        path = raw_grobid / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            issues.append(f"{name} malformed")
            continue

        if name == "citations.json":
            totals["citations"] += len(data)
            linked = sum(1 for c in data if c.get("ref_id"))
            totals["citations_linked"] += linked
            print(f"  citations: {len(data)} total ({linked} linked to bibliography)")
        elif name == "sections.json":
            totals["grobid_sections"] += len(data)
            print(f"  sections: {len(data)}")
        elif name == "figures.json":
            totals["grobid_figures"] += len(data)
            print(f"  figures: {len(data)}")

    return issues


if __name__ == "__main__":
    main()
