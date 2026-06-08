"""GROBID extractor.

Two extraction modes:

  extract()         — POST to processReferences. Fast, bibliography only.
  extract_fulltext() — POST to processFulltextDocument. Slower, but returns
                       the full document structure: sections, inline citations
                       linked to bibliography entries, and figure/table captions.

Both write to raw/grobid/. The fulltext mode is a strict superset — it
produces everything extract() does plus the additional artifacts. When
both are available, downstream consumers should prefer the fulltext artifacts.

Normalized outputs owned by this extractor:
  - references.tei.xml / references.csl.json  (bibliography)
  - fulltext.tei.xml                          (complete document TEI)
  - citations.json                            (in-text citation anchors)
  - sections.json                             (document section headings)
  - figures.json                              (figure/table catalog)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from pydantic import BaseModel, ConfigDict, Field


TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


TEI_NSMAP = "http://www.tei-c.org/ns/1.0"


class CSLName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: str | None = None
    given: str | None = None
    literal: str | None = None


class CSLDate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    date_parts: list[list[int]] = Field(alias="date-parts")


class CSLReference(BaseModel):
    """A conservative subset of CSL-JSON bibliographic item fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    type: str
    title: str | None = None
    author: list[CSLName] | None = None
    issued: CSLDate | None = None
    container_title: str | None = Field(default=None, alias="container-title")
    volume: str | None = None
    issue: str | None = None
    page: str | None = None
    publisher: str | None = None
    publisher_place: str | None = Field(default=None, alias="publisher-place")
    DOI: str | None = None
    URL: str | None = None
    note: str | None = None


class Citation(BaseModel):
    """An in-text citation anchor linking body text to a bibliography entry."""

    model_config = ConfigDict(extra="forbid")

    text: str
    ref_id: str | None = None
    context: str
    section: str | None = None


class SectionHeading(BaseModel):
    """A document section extracted from TEI structure."""

    model_config = ConfigDict(extra="forbid")

    title: str
    level: int
    parent: str | None = None


class Figure(BaseModel):
    """A figure or table entry from TEI."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    label: str | None = None
    caption: str | None = None
    type: str = "figure"


def extract(
    source: Path,
    book_dir: Path,
    *,
    base_url: str,
    timeout: float = 180.0,
    consolidate_citations: str = "0",
) -> list[CSLReference]:
    """POST source to GROBID, write raw TEI + normalized CSL-JSON.

    Returns the parsed CSL references. Raises on any failure.

    Writes:
      - book_dir/raw/grobid/references.tei.xml  (native TEI)
      - book_dir/raw/grobid/references.csl.json (normalized CSL-JSON)

    HTTP 204 (no references found) writes an empty listBibl + empty CSL list.
    """
    if not source.exists():
        raise FileNotFoundError(source)

    with source.open("rb") as pdf:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/processReferences",
            headers={"Accept": "application/xml"},
            files={"input": (source.name, pdf, "application/pdf")},
            data={
                "includeRawCitations": "1",
                "consolidateCitations": consolidate_citations,
            },
            timeout=timeout,
        )

    if response.status_code == 204:
        tei = '<listBibl xmlns="http://www.tei-c.org/ns/1.0" />'
    else:
        response.raise_for_status()
        tei = response.text

    out_dir = book_dir / "raw" / "grobid"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "references.tei.xml").write_text(tei)

    references = tei_to_csl(tei)
    (out_dir / "references.csl.json").write_text(
        json.dumps(
            [ref.model_dump(by_alias=True, exclude_none=True) for ref in references],
            indent=2,
        )
        + "\n"
    )
    return references


class FulltextResult(BaseModel):
    """Everything extracted from a GROBID processFulltextDocument call."""

    model_config = ConfigDict(extra="forbid")

    references: list[CSLReference]
    citations: list[Citation]
    sections: list[SectionHeading]
    figures: list[Figure]

    # Document-level metadata from TEI header
    header_title: str | None = None
    header_authors: list[str] = Field(default_factory=list)
    header_year: int | None = None


def extract_fulltext(
    source: Path,
    book_dir: Path,
    *,
    base_url: str,
    timeout: float = 300.0,
    consolidate_citations: str = "0",
) -> FulltextResult:
    """POST source to GROBID processFulltextDocument.

    Returns parsed fulltext result. Raises on any failure.

    Writes:
      - book_dir/raw/grobid/fulltext.tei.xml   (complete document TEI)
      - book_dir/raw/grobid/references.tei.xml  (bibliography subset for compat)
      - book_dir/raw/grobid/references.csl.json (normalized CSL-JSON)
      - book_dir/raw/grobid/citations.json      (in-text citation anchors)
      - book_dir/raw/grobid/sections.json       (document section headings)
      - book_dir/raw/grobid/figures.json         (figure/table catalog)

    HTTP 204 writes empty artifacts.
    """
    if not source.exists():
        raise FileNotFoundError(source)

    with source.open("rb") as pdf:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/processFulltextDocument",
            headers={"Accept": "application/xml"},
            files={"input": (source.name, pdf, "application/pdf")},
            data={
                "includeRawCitations": "1",
                "consolidateCitations": consolidate_citations,
            },
            timeout=timeout,
        )

    # Parse BEFORE writing anything, so a bad response (malformed XML, or a
    # non-TEI 200 error page) raises instead of leaving a partial artifact set.
    if response.status_code == 204:
        tei = (
            f'<TEI xmlns="{TEI_NSMAP}"><teiHeader/>'
            f"<text><body/><back><listBibl/></back></text></TEI>"
        )
        result = FulltextResult(references=[], citations=[], sections=[], figures=[])
    else:
        response.raise_for_status()
        tei = response.text
        result = parse_fulltext_tei(tei)

    out_dir = book_dir / "raw" / "grobid"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fulltext.tei.xml").write_text(tei)

    # Write compat references artifacts (same as extract())
    ref_tei = _extract_listbibl_xml(out_dir / "fulltext.tei.xml")
    (out_dir / "references.tei.xml").write_text(ref_tei)
    (out_dir / "references.csl.json").write_text(
        json.dumps(
            [r.model_dump(by_alias=True, exclude_none=True) for r in result.references],
            indent=2,
        )
        + "\n"
    )

    def _dump(data: list, path: Path) -> None:
        path.write_text(
            json.dumps([item.model_dump(exclude_none=True) for item in data], indent=2)
            + "\n"
        )

    _dump(result.citations, out_dir / "citations.json")
    _dump(result.sections, out_dir / "sections.json")
    _dump(result.figures, out_dir / "figures.json")

    return result


def parse_fulltext_tei(tei_xml: str) -> FulltextResult:
    """Parse a complete GROBID fulltext TEI document.

    Raises ValueError if the payload is not a TEI document — e.g. GROBID (or a
    proxy in front of it) returned HTTP 200 with an HTML error/queue page. That
    must surface as an error rather than be silently parsed to empty results.
    """
    root = ET.fromstring(tei_xml)
    if _strip_ns(root.tag) != "TEI":
        raise ValueError(
            f"expected a TEI document, got root <{_strip_ns(root.tag)}> "
            "(GROBID may have returned a non-TEI error response)"
        )

    references = _parse_references(root)
    ref_id_map = _build_ref_id_map(root)
    citations = _parse_citations(root, ref_id_map)
    sections = _parse_sections(root)
    figures = _parse_figures(root)
    header_title, header_authors, header_year = _parse_header_metadata(root)

    return FulltextResult(
        references=references,
        citations=citations,
        sections=sections,
        figures=figures,
        header_title=header_title,
        header_authors=header_authors,
        header_year=header_year,
    )


def _parse_header_metadata(
    root: ET.Element,
) -> tuple[str | None, list[str], int | None]:
    """Extract title, authors, year from the TEI header."""
    header = root.find(f".//{{{TEI_NSMAP}}}teiHeader")
    if header is None:
        return None, [], None

    title_el = header.find(f".//{{{TEI_NSMAP}}}titleStmt/{{{TEI_NSMAP}}}title")
    title = _text_or_none(title_el)

    bibl = header.find(
        f".//{{{TEI_NSMAP}}}sourceDesc/{{{TEI_NSMAP}}}biblStruct"
    )
    author_names: list[str] = []
    year: int | None = None
    if bibl is not None:
        for name in _authors(bibl):
            if name.literal:
                author_names.append(name.literal)
            elif name.given and name.family:
                author_names.append(f"{name.given} {name.family}")
            elif name.family:
                author_names.append(name.family)

        date = _issued_date(bibl)
        if date and date.date_parts and date.date_parts[0]:
            year = date.date_parts[0][0]

    return title, author_names, year


def _parse_references(root: ET.Element) -> list[CSLReference]:
    """Extract bibliography from a fulltext TEI document."""
    bibl_structs = root.findall(".//tei:back//tei:listBibl/tei:biblStruct", TEI_NS)
    return [_bibl_to_csl(bibl, i) for i, bibl in enumerate(bibl_structs, start=1)]


def _build_ref_id_map(root: ET.Element) -> dict[str, str]:
    """Map xml:id values (e.g. 'b0') to our normalized ref-N ids."""
    mapping: dict[str, str] = {}
    bibl_structs = root.findall(".//tei:back//tei:listBibl/tei:biblStruct", TEI_NS)
    for i, bibl in enumerate(bibl_structs, start=1):
        xml_id = bibl.attrib.get(XML_ID)
        if xml_id:
            mapping[xml_id] = _reference_id(bibl, i)
    return mapping


def _parse_citations(
    root: ET.Element, ref_id_map: dict[str, str]
) -> list[Citation]:
    """Extract in-text citation anchors from body <ref type="bibr"> tags."""
    body = root.find(f".//{{{TEI_NSMAP}}}body")
    if body is None:
        return []

    citations: list[Citation] = []
    _walk_divs_for_citations(body, citations, ref_id_map, section=None)
    return citations


def _walk_divs_for_citations(
    element: ET.Element,
    out: list[Citation],
    ref_id_map: dict[str, str],
    section: str | None,
) -> None:
    for div in element.findall(f"{{{TEI_NSMAP}}}div"):
        head = div.find(f"{{{TEI_NSMAP}}}head")
        current_section = _text_or_none(head) if head is not None else section

        for p in div.findall(f"{{{TEI_NSMAP}}}p"):
            context = _text_or_none(p) or ""
            for ref in p.findall(f"{{{TEI_NSMAP}}}ref"):
                if ref.attrib.get("type") != "bibr":
                    continue
                cite_text = _text_or_none(ref) or ""
                if not cite_text:
                    continue

                target = ref.attrib.get("target", "")
                raw_id = target.lstrip("#") if target else None
                ref_id = ref_id_map.get(raw_id) if raw_id else None

                out.append(Citation(
                    text=cite_text,
                    ref_id=ref_id,
                    context=context,
                    section=current_section,
                ))

        _walk_divs_for_citations(div, out, ref_id_map, current_section)


def _parse_sections(root: ET.Element) -> list[SectionHeading]:
    """Extract section headings from body <div> / <head> structure."""
    body = root.find(f".//{{{TEI_NSMAP}}}body")
    if body is None:
        return []

    sections: list[SectionHeading] = []
    _walk_divs_for_sections(body, sections, level=1, parent=None)
    return sections


def _walk_divs_for_sections(
    element: ET.Element,
    out: list[SectionHeading],
    level: int,
    parent: str | None,
) -> None:
    for div in element.findall(f"{{{TEI_NSMAP}}}div"):
        head = div.find(f"{{{TEI_NSMAP}}}head")
        title = _text_or_none(head) if head is not None else None
        if title:
            out.append(SectionHeading(title=title, level=level, parent=parent))
            _walk_divs_for_sections(div, out, level + 1, parent=title)
        else:
            _walk_divs_for_sections(div, out, level, parent=parent)


def _parse_figures(root: ET.Element) -> list[Figure]:
    """Extract figure and table entries from <figure> elements."""
    figures: list[Figure] = []
    for fig in root.findall(f".//{{{TEI_NSMAP}}}figure"):
        fig_id = fig.attrib.get(XML_ID)
        label_el = fig.find(f"{{{TEI_NSMAP}}}label")
        desc_el = fig.find(f"{{{TEI_NSMAP}}}figDesc")
        head_el = fig.find(f"{{{TEI_NSMAP}}}head")

        label = _text_or_none(label_el) or _text_or_none(head_el)
        caption = _text_or_none(desc_el)

        if not label and not caption:
            continue

        fig_type = fig.attrib.get("type", "figure")

        figures.append(Figure(
            id=fig_id,
            label=label,
            caption=caption,
            type=fig_type,
        ))

    return figures


def _extract_listbibl_xml(fulltext_path: Path) -> str:
    """Pull the <listBibl> out of a fulltext TEI and return standalone XML."""
    tree = ET.parse(fulltext_path)
    root = tree.getroot()
    list_bibl = root.find(f".//{{{TEI_NSMAP}}}listBibl")
    if list_bibl is None:
        return f'<listBibl xmlns="{TEI_NSMAP}" />'
    return ET.tostring(list_bibl, encoding="unicode")


def tei_to_csl(tei_xml: str) -> list[CSLReference]:
    """Parse a TEI listBibl document into CSL-JSON records."""
    root = ET.fromstring(tei_xml)
    bibl_structs = root.findall(".//tei:biblStruct", TEI_NS)
    if _strip_ns(root.tag) == "biblStruct":
        bibl_structs = [root]
    return [_bibl_to_csl(bibl, i) for i, bibl in enumerate(bibl_structs, start=1)]


def _bibl_to_csl(bibl: ET.Element, index: int) -> CSLReference:
    raw = _raw_reference(bibl)
    article_title = _text_or_none(bibl.find("./tei:analytic/tei:title", TEI_NS))
    container_title = _text_or_none(bibl.find("./tei:monogr/tei:title", TEI_NS))

    return CSLReference(
        id=_reference_id(bibl, index),
        type=_csl_type(container_title=container_title, article_title=article_title),
        title=article_title or raw,
        author=_authors(bibl) or None,
        issued=_issued_date(bibl),
        container_title=container_title,
        volume=_bibl_scope(bibl, "volume"),
        issue=_bibl_scope(bibl, "issue"),
        page=_page_scope(bibl),
        publisher=_text_or_none(bibl.find("./tei:monogr/tei:imprint/tei:publisher", TEI_NS)),
        publisher_place=_text_or_none(bibl.find("./tei:monogr/tei:imprint/tei:pubPlace", TEI_NS)),
        DOI=_idno(bibl, "DOI"),
        URL=_idno(bibl, "URL"),
        note=f"Raw reference: {raw}" if raw else None,
    )


def _reference_id(bibl: ET.Element, index: int) -> str:
    xml_id = bibl.attrib.get(XML_ID)
    if xml_id:
        match = re.search(r"(\d+)$", xml_id)
        if match:
            return f"ref-{int(match.group(1)) + 1}"
    return f"ref-{index}"


def _csl_type(*, container_title: str | None, article_title: str | None) -> str:
    if container_title and article_title:
        return "article-journal"
    if container_title:
        return "chapter"
    return "article"


def _authors(bibl: ET.Element) -> list[CSLName]:
    nodes = bibl.findall("./tei:analytic/tei:author", TEI_NS)
    if not nodes:
        nodes = bibl.findall("./tei:monogr/tei:author", TEI_NS)

    names: list[CSLName] = []
    for node in nodes:
        pers_name = node.find("./tei:persName", TEI_NS)
        if pers_name is None:
            # Skip affiliation-only author tags (no person name, just org/address)
            if node.find("./tei:affiliation", TEI_NS) is not None:
                continue
            if literal := _text_or_none(node):
                names.append(CSLName(literal=literal))
            continue

        family = _text_or_none(pers_name.find("./tei:surname", TEI_NS))
        given_parts = [
            text
            for text in (_text_or_none(item) for item in pers_name.findall("./tei:forename", TEI_NS))
            if text
        ]
        given = " ".join(given_parts) if given_parts else None
        if family or given:
            names.append(CSLName(family=family, given=given))
        elif literal := _text_or_none(pers_name):
            names.append(CSLName(literal=literal))
    return names


def _issued_date(bibl: ET.Element) -> CSLDate | None:
    date = bibl.find("./tei:monogr/tei:imprint/tei:date", TEI_NS)
    if date is None:
        date = bibl.find(".//tei:date", TEI_NS)
    if date is None:
        return None
    value = date.attrib.get("when") or _text_or_none(date) or ""
    match = re.search(r"\d{4}", value)
    if not match:
        return None
    return CSLDate(date_parts=[[int(match.group(0))]])


def _bibl_scope(bibl: ET.Element, unit: str) -> str | None:
    return _text_or_none(
        bibl.find(f"./tei:monogr/tei:imprint/tei:biblScope[@unit='{unit}']", TEI_NS)
    )


def _page_scope(bibl: ET.Element) -> str | None:
    node = bibl.find("./tei:monogr/tei:imprint/tei:biblScope[@unit='page']", TEI_NS)
    if node is None:
        return None
    start = node.attrib.get("from")
    end = node.attrib.get("to")
    if start and end:
        return f"{start}-{end}"
    return _text_or_none(node)


def _idno(bibl: ET.Element, id_type: str) -> str | None:
    for node in bibl.findall(".//tei:idno", TEI_NS):
        if node.attrib.get("type", "").lower() == id_type.lower():
            return _text_or_none(node)
    return None


def _raw_reference(bibl: ET.Element) -> str | None:
    for note in bibl.findall(".//tei:note", TEI_NS):
        if note.attrib.get("type") == "raw_reference":
            return _text_or_none(note)
    return None


def _text_or_none(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    text = " ".join("".join(node.itertext()).split())
    return text or None


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run GROBID on a PDF, write raw TEI + normalized artifacts to raw/grobid/."
    )
    parser.add_argument("source_pdf", type=Path, help="Source PDF")
    parser.add_argument("book_dir", type=Path, help="Per-book output directory")
    parser.add_argument(
        "--grobid-url",
        default=None,
        help="GROBID service root URL. Defaults to GROBID_BASE_URL.",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--consolidate-citations",
        choices=["0", "1", "2"],
        default="0",
        help="GROBID citation consolidation mode.",
    )
    parser.add_argument(
        "--refs-only",
        action="store_true",
        help="Use processReferences instead of processFulltextDocument.",
    )
    args = parser.parse_args()

    base_url = args.grobid_url or os.getenv("GROBID_BASE_URL")
    if not base_url:
        print("Set --grobid-url or GROBID_BASE_URL", file=sys.stderr)
        raise SystemExit(2)

    if args.refs_only:
        references = extract(
            args.source_pdf,
            args.book_dir,
            base_url=base_url,
            timeout=args.timeout,
            consolidate_citations=args.consolidate_citations,
        )
        print(json.dumps({"references": len(references)}, indent=2))
    else:
        result = extract_fulltext(
            args.source_pdf,
            args.book_dir,
            base_url=base_url,
            timeout=args.timeout,
            consolidate_citations=args.consolidate_citations,
        )
        print(json.dumps({
            "references": len(result.references),
            "citations": len(result.citations),
            "sections": len(result.sections),
            "figures": len(result.figures),
        }, indent=2))


if __name__ == "__main__":
    main()
