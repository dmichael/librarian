"""References domain builder.

Reads the TEI XML written by the GROBID extractor (raw/grobid/references.tei.xml)
and produces the clean canonical bibliography (clean/references.csl.json) in
CSL-JSON. The HTTP call to GROBID lives in librarian.extractors.grobid; this
module owns only the TEI→CSL conversion and the clean output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from librarian.extractors import grobid

import os


TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


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


class ReferencesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_tei_path: str
    csl_json_path: str
    count: int


def build_references(book_dir: Path) -> ReferencesResult:
    """Convert raw/grobid/references.tei.xml → clean/references.csl.json.

    Raises FileNotFoundError if the GROBID extractor hasn't run.
    """
    raw_path = book_dir / "raw" / "grobid" / "references.tei.xml"
    if not raw_path.exists():
        raise FileNotFoundError(
            f"GROBID raw TEI not found at {raw_path}; run the grobid extractor first"
        )

    tei_xml = raw_path.read_text()
    references = tei_references_to_csl(tei_xml)

    clean_dir = book_dir / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    csl_path = clean_dir / "references.csl.json"
    csl_path.write_text(
        json.dumps(
            [item.model_dump(by_alias=True, exclude_none=True) for item in references],
            indent=2,
        )
        + "\n"
    )
    return ReferencesResult(
        raw_tei_path=str(raw_path),
        csl_json_path=str(csl_path),
        count=len(references),
    )


def tei_references_to_csl(tei_xml: str) -> list[CSLReference]:
    root = ET.fromstring(tei_xml)
    bibl_structs = root.findall(".//tei:biblStruct", TEI_NS)
    if _strip_ns(root.tag) == "biblStruct":
        bibl_structs = [root]

    references = []
    for index, bibl in enumerate(bibl_structs, start=1):
        references.append(_bibl_struct_to_csl(bibl, index))
    return references


def _bibl_struct_to_csl(bibl: ET.Element, index: int) -> CSLReference:
    raw = _raw_reference(bibl)
    article_title = _text_or_none(bibl.find("./tei:analytic/tei:title", TEI_NS))
    container_title = _text_or_none(bibl.find("./tei:monogr/tei:title", TEI_NS))
    authors = _authors(bibl)
    issued = _issued_date(bibl)
    volume = _bibl_scope(bibl, "volume")
    issue = _bibl_scope(bibl, "issue")
    page = _page_scope(bibl)
    doi = _idno(bibl, "DOI")
    url = _idno(bibl, "URL")

    return CSLReference(
        id=_reference_id(bibl, index),
        type=_csl_type(container_title=container_title, article_title=article_title),
        title=article_title or raw,
        author=authors or None,
        issued=issued,
        container_title=container_title,
        volume=volume,
        issue=issue,
        page=page,
        publisher=_text_or_none(bibl.find("./tei:monogr/tei:imprint/tei:publisher", TEI_NS)),
        publisher_place=_text_or_none(bibl.find("./tei:monogr/tei:imprint/tei:pubPlace", TEI_NS)),
        DOI=doi,
        URL=url,
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

    names = []
    for node in nodes:
        pers_name = node.find("./tei:persName", TEI_NS)
        if pers_name is None:
            literal = _text_or_none(node)
            if literal:
                names.append(CSLName(literal=literal))
            continue

        family = _text_or_none(pers_name.find("./tei:surname", TEI_NS))
        given_parts = [
            text
            for text in (
                _text_or_none(item) for item in pers_name.findall("./tei:forename", TEI_NS)
            )
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
    node = bibl.find(f"./tei:monogr/tei:imprint/tei:biblScope[@unit='{unit}']", TEI_NS)
    return _text_or_none(node)


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
        description="Build clean/references.csl.json from a PDF. Runs the GROBID "
        "extractor (raw/grobid/) then the references domain builder."
    )
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

    base_url = args.grobid_url or os.getenv("GROBID_BASE_URL")
    if not base_url:
        print("Set --grobid-url or GROBID_BASE_URL", file=sys.stderr)
        raise SystemExit(2)

    try:
        grobid.extract(
            args.source_pdf,
            args.book_dir,
            base_url=base_url,
            timeout=args.timeout,
            consolidate_citations=args.consolidate_citations,
        )
        result = build_references(args.book_dir)
    except Exception as exc:
        print(f"reference build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()

