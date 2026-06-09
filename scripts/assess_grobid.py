"""Assess GROBID's per-document contribution across the test fixtures.

Reports, for each extracted fixture: GROBID references/citations/sections/figures
yield and the parsed header title, alongside Marker block/equation counts — so we
can judge whether GROBID is a net positive and for which document types.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "http://www.tei-c.org/ns/1.0"
ROOT = Path("tests/fixtures/extracted")


def count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(json.loads(path.read_text()))
    except Exception:
        return -1


def header_title(tei_path: Path) -> str:
    if not tei_path.exists():
        return "(no tei)"
    try:
        root = ET.fromstring(tei_path.read_text())
    except Exception:
        return "(parse error)"
    el = root.find(f".//{{{NS}}}teiHeader//{{{NS}}}titleStmt/{{{NS}}}title")
    return (el.text or "").strip() if el is not None and el.text else "(none)"


def marker_counts(marker_dir: Path) -> tuple[int, int]:
    doc = marker_dir / "document.json"
    eqs = marker_dir / "equations.json"
    blocks = 0
    if doc.exists():
        try:
            data = json.loads(doc.read_text())
            blocks = len(data.get("blocks", data if isinstance(data, list) else []))
        except Exception:
            blocks = -1
    return blocks, count(eqs)


def main() -> None:
    rows = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir():
            continue
        g = d / "raw" / "grobid"
        m = d / "raw" / "marker"
        refs = count(g / "references.csl.json")
        cites = count(g / "citations.json")
        secs = count(g / "sections.json")
        figs = count(g / "figures.json")
        title = header_title(g / "fulltext.tei.xml")
        blocks, eqs = marker_counts(m)
        rows.append((d.name[:34], refs, cites, secs, figs, blocks, eqs, title[:50]))

    hdr = f"{'fixture':34} {'refs':>4} {'cite':>4} {'sec':>4} {'fig':>4} {'blk':>5} {'eq':>4}  header_title"
    print(hdr)
    print("-" * len(hdr))
    for name, refs, cites, secs, figs, blocks, eqs, title in rows:
        print(f"{name:34} {refs:>4} {cites:>4} {secs:>4} {figs:>4} {blocks:>5} {eqs:>4}  {title}")


if __name__ == "__main__":
    main()
