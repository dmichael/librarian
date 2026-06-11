"""Tests for GROBID fulltext extraction: citations, sections, figures."""

import json
from pathlib import Path

from librarian.extractors.grobid import (
    Citation,
    Figure,
    FulltextResult,
    SectionHeading,
    extract_fulltext,
    parse_fulltext_tei,
)

SAMPLE_FULLTEXT_TEI = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title level="a" type="main">Test Paper</title></titleStmt>
      <publicationStmt><publisher/></publicationStmt>
      <sourceDesc><biblStruct><analytic>
        <author><persName><forename>A.</forename><surname>Test</surname></persName></author>
        <title level="a" type="main">Test Paper</title>
      </analytic><monogr><imprint><date/></imprint></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div xmlns="http://www.tei-c.org/ns/1.0">
        <head>Introduction</head>
        <p>Previous work by <ref type="bibr" target="#b0">Smith (2006)</ref> showed
        that oscillators synchronize. See also <ref type="bibr" target="#b1">Jones and
        Lee (2010)</ref> for a review. Figure <ref type="figure" target="#fig_0">1</ref>
        shows the setup.</p>
      </div>
      <div xmlns="http://www.tei-c.org/ns/1.0">
        <head>Methods</head>
        <p>We followed the protocol of <ref type="bibr" target="#b0">Smith (2006)</ref>.</p>
        <div xmlns="http://www.tei-c.org/ns/1.0">
          <head>Data Collection</head>
          <p>Recordings were made in the field.</p>
        </div>
        <div xmlns="http://www.tei-c.org/ns/1.0">
          <head>Analysis</head>
          <p>We used spectrograms as in <ref type="bibr">Greenfield (1994)</ref>.</p>
        </div>
      </div>
      <div xmlns="http://www.tei-c.org/ns/1.0">
        <head>Results</head>
        <p>The data confirmed the hypothesis.</p>
      </div>
      <figure xmlns="http://www.tei-c.org/ns/1.0" xml:id="fig_0">
        <head>Fig. 1</head>
        <label>1</label>
        <figDesc>Experimental setup showing microphone placement.</figDesc>
      </figure>
      <figure xmlns="http://www.tei-c.org/ns/1.0" xml:id="tab_0" type="table">
        <head>Table 1</head>
        <label>1</label>
        <figDesc>Summary of call parameters across populations.</figDesc>
      </figure>
    </body>
    <back>
      <div type="references">
        <listBibl>
          <biblStruct xml:id="b0">
            <analytic>
              <author><persName><forename>J.</forename><surname>Smith</surname></persName></author>
              <title level="a">Oscillator synchrony in frogs</title>
            </analytic>
            <monogr>
              <title level="j">J. Acoust.</title>
              <imprint><date when="2006"/></imprint>
            </monogr>
            <note type="raw_reference">Smith, J. 2006. Oscillator synchrony in frogs. J. Acoust.</note>
          </biblStruct>
          <biblStruct xml:id="b1">
            <analytic>
              <author><persName><forename>R.</forename><surname>Jones</surname></persName></author>
              <author><persName><forename>K.</forename><surname>Lee</surname></persName></author>
              <title level="a">A review of acoustic interactions</title>
            </analytic>
            <monogr>
              <title level="j">Ann. Rev. Ecol.</title>
              <imprint>
                <biblScope unit="volume">41</biblScope>
                <date when="2010"/>
              </imprint>
            </monogr>
            <note type="raw_reference">Jones, R. and K. Lee. 2010. A review. Ann. Rev. Ecol. 41.</note>
          </biblStruct>
        </listBibl>
      </div>
    </back>
  </text>
</TEI>
"""


def test_parse_fulltext_tei_references():
    result = parse_fulltext_tei(SAMPLE_FULLTEXT_TEI)
    assert len(result.references) == 2
    assert result.references[0].id == "ref-1"
    assert result.references[0].title == "Oscillator synchrony in frogs"
    assert result.references[1].id == "ref-2"
    assert result.references[1].volume == "41"


def test_parse_fulltext_tei_citations():
    result = parse_fulltext_tei(SAMPLE_FULLTEXT_TEI)
    assert len(result.citations) == 4

    by_text = {c.text: c for c in result.citations}

    smith_intro = [c for c in result.citations if "Smith" in c.text and c.section == "Introduction"]
    assert len(smith_intro) == 1
    assert smith_intro[0].ref_id == "ref-1"

    jones = [c for c in result.citations if "Jones" in c.text]
    assert len(jones) == 1
    assert jones[0].ref_id == "ref-2"
    assert jones[0].section == "Introduction"

    smith_methods = [c for c in result.citations if "Smith" in c.text and c.section == "Methods"]
    assert len(smith_methods) == 1
    assert smith_methods[0].ref_id == "ref-1"

    greenfield = [c for c in result.citations if "Greenfield" in c.text]
    assert len(greenfield) == 1
    assert greenfield[0].ref_id is None
    assert greenfield[0].section == "Analysis"


def test_parse_fulltext_tei_sections():
    result = parse_fulltext_tei(SAMPLE_FULLTEXT_TEI)
    titles = [s.title for s in result.sections]
    assert "Introduction" in titles
    assert "Methods" in titles
    assert "Results" in titles
    assert "Data Collection" in titles
    assert "Analysis" in titles

    methods = next(s for s in result.sections if s.title == "Methods")
    assert methods.level == 1

    data_coll = next(s for s in result.sections if s.title == "Data Collection")
    assert data_coll.level == 2
    assert data_coll.parent == "Methods"


def test_parse_fulltext_tei_figures():
    result = parse_fulltext_tei(SAMPLE_FULLTEXT_TEI)
    assert len(result.figures) == 2

    fig = next(f for f in result.figures if f.id == "fig_0")
    assert fig.label == "1"
    assert "microphone" in fig.caption
    assert fig.type == "figure"

    tab = next(f for f in result.figures if f.id == "tab_0")
    assert tab.type == "table"
    assert "call parameters" in tab.caption


def test_parse_fulltext_tei_empty():
    tei = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader/><text><body/><back><listBibl/></back></text></TEI>'
    result = parse_fulltext_tei(tei)
    assert result.references == []
    assert result.citations == []
    assert result.sections == []
    assert result.figures == []


def test_extract_fulltext_writes_all_artifacts(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    class FakeResponse:
        status_code = 200
        text = SAMPLE_FULLTEXT_TEI

        def raise_for_status(self):
            return None

    captured = {}

    def fake_post(url, headers, files, data, timeout):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr("librarian.extractors.grobid.httpx.post", fake_post)

    result = extract_fulltext(pdf, tmp_path, base_url="http://grobid.test:8070")

    assert captured["url"] == "http://grobid.test:8070/api/processFulltextDocument"

    grobid_dir = tmp_path / "raw" / "grobid"
    assert (grobid_dir / "fulltext.tei.xml").exists()
    assert (grobid_dir / "references.tei.xml").exists()
    assert (grobid_dir / "references.csl.json").exists()
    assert (grobid_dir / "citations.json").exists()
    assert (grobid_dir / "sections.json").exists()
    assert (grobid_dir / "figures.json").exists()

    csl = json.loads((grobid_dir / "references.csl.json").read_text())
    assert len(csl) == 2

    citations = json.loads((grobid_dir / "citations.json").read_text())
    assert len(citations) == 4

    sections = json.loads((grobid_dir / "sections.json").read_text())
    assert any(s["title"] == "Methods" for s in sections)

    figures = json.loads((grobid_dir / "figures.json").read_text())
    assert len(figures) == 2


def test_extract_fulltext_204_writes_empty_artifacts(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    class FakeResponse:
        status_code = 204
        text = ""

    monkeypatch.setattr(
        "librarian.extractors.grobid.httpx.post",
        lambda *args, **kwargs: FakeResponse(),
    )

    result = extract_fulltext(pdf, tmp_path, base_url="http://grobid.test:8070")

    assert result.references == []
    assert result.citations == []

    grobid_dir = tmp_path / "raw" / "grobid"
    assert json.loads((grobid_dir / "citations.json").read_text()) == []
    assert json.loads((grobid_dir / "sections.json").read_text()) == []
    assert json.loads((grobid_dir / "figures.json").read_text()) == []


def test_citations_captured_outside_div_p():
    """Refs nested in <s> sentences and in figure captions must be captured."""
    tei = """\
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text><body>
    <div>
      <head>Results</head>
      <p><s>As shown by <ref type="bibr" target="#b0">Smith (2006)</ref>.</s></p>
    </div>
    <figure xml:id="fig_0">
      <figDesc>Adapted from <ref type="bibr" target="#b1">Jones (2010)</ref>.</figDesc>
    </figure>
  </body></text>
</TEI>"""
    result = parse_fulltext_tei(tei)
    texts = {c.text for c in result.citations}
    assert "Smith (2006)" in texts  # nested in <s>, missed by div>p walker before
    assert "Jones (2010)" in texts  # inside <figDesc>, never under <div>/<p>

    smith = next(c for c in result.citations if c.text == "Smith (2006)")
    assert smith.section == "Results"


def test_parse_fulltext_tei_rejects_non_tei():
    import pytest
    # A 200 OK carrying an HTML error/queue page must not parse to empty results.
    with pytest.raises(ValueError):
        parse_fulltext_tei("<html><body>503 Service Unavailable</body></html>")


def test_extract_fulltext_500_error_includes_body(tmp_path: Path, monkeypatch):
    import httpx
    import pytest

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    # GROBID's failure reason lives in the response body — the raised error
    # must carry it, not just the bare status code and URL.
    url = "http://grobid.test:8070/api/processFulltextDocument"
    response = httpx.Response(
        500,
        text="[GENERAL] An exception occurred while running Grobid: timeout",
        request=httpx.Request("POST", url),
    )
    monkeypatch.setattr(
        "librarian.extractors.grobid.httpx.post",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(httpx.HTTPStatusError, match="exception occurred while running Grobid"):
        extract_fulltext(pdf, tmp_path, base_url="http://grobid.test:8070")


def test_extract_fulltext_non_tei_200_raises_and_writes_nothing(tmp_path: Path, monkeypatch):
    import pytest

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    class FakeResponse:
        status_code = 200
        text = "<html><body>Internal Server Error</body></html>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "librarian.extractors.grobid.httpx.post",
        lambda *args, **kwargs: FakeResponse(),
    )

    with pytest.raises(ValueError):
        extract_fulltext(pdf, tmp_path, base_url="http://grobid.test:8070")

    # No partial artifact set left behind (parse happens before any write).
    grobid_dir = tmp_path / "raw" / "grobid"
    assert not (grobid_dir / "fulltext.tei.xml").exists()
    assert not (grobid_dir / "citations.json").exists()


def test_fulltext_result_serialization():
    result = FulltextResult(
        references=[],
        citations=[Citation(text="Smith (2006)", ref_id="ref-1", context="as shown by Smith (2006)", section="Intro")],
        sections=[SectionHeading(title="Intro", level=1)],
        figures=[Figure(id="fig_0", label="1", caption="Setup")],
    )
    d = result.model_dump(exclude_none=True)
    assert d["citations"][0]["text"] == "Smith (2006)"
    assert d["citations"][0]["ref_id"] == "ref-1"
    assert d["sections"][0]["title"] == "Intro"
    assert d["figures"][0]["type"] == "figure"
