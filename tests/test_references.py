import json
from pathlib import Path

from librarian.extractors import grobid


SAMPLE_TEI = """\
<listBibl xmlns="http://www.tei-c.org/ns/1.0">
  <biblStruct xml:id="b0">
    <analytic>
      <author>
        <persName>
          <forename type="first">N.</forename>
          <forename type="middle">H.</forename>
          <surname>Fletcher</surname>
        </persName>
      </author>
      <title level="a">Acoustical Systems in Biology</title>
    </analytic>
    <monogr>
      <title level="j">Oxford University Press</title>
      <imprint>
        <date when="1992" />
      </imprint>
    </monogr>
    <note type="raw_reference">N. H. Fletcher, Acoustical Systems in Biology (Oxford University Press, Oxford, 1992).</note>
  </biblStruct>
  <biblStruct xml:id="b17">
    <analytic>
      <author>
        <persName>
          <forename type="first">J.</forename>
          <forename type="middle">L.</forename>
          <surname>Flanagan</surname>
        </persName>
      </author>
      <title level="a">Some speech paper</title>
    </analytic>
    <monogr>
      <title level="j">J. Acoust. Soc. Am.</title>
      <imprint>
        <biblScope unit="volume">30</biblScope>
        <biblScope unit="page" from="957" to="962" />
        <date when="1958" />
      </imprint>
    </monogr>
  </biblStruct>
</listBibl>
"""


def test_tei_to_csl_projects_grobid_tei_to_csl_json():
    refs = grobid.tei_to_csl(SAMPLE_TEI)

    assert len(refs) == 2
    first = refs[0].model_dump(by_alias=True, exclude_none=True)
    assert first["id"] == "ref-1"
    assert first["type"] == "article-journal"
    assert first["author"] == [{"family": "Fletcher", "given": "N. H."}]
    assert first["title"] == "Acoustical Systems in Biology"
    assert first["container-title"] == "Oxford University Press"
    assert first["issued"] == {"date-parts": [[1992]]}
    assert first["note"].startswith("Raw reference:")

    second = refs[1].model_dump(by_alias=True, exclude_none=True)
    assert second["id"] == "ref-18"
    assert second["container-title"] == "J. Acoust. Soc. Am."
    assert second["volume"] == "30"
    assert second["page"] == "957-962"


def test_grobid_extract_writes_raw_tei_and_normalized_csl(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    captured = {}

    class FakeResponse:
        status_code = 200
        text = SAMPLE_TEI

        def raise_for_status(self):
            return None

    def fake_post(url, headers, files, data, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("librarian.extractors.grobid.httpx.post", fake_post)

    refs = grobid.extract(
        pdf,
        tmp_path,
        base_url="http://grobid.local:8070",
        timeout=30,
        consolidate_citations="2",
    )

    assert len(refs) == 2
    assert (tmp_path / "raw" / "grobid" / "references.tei.xml").read_text() == SAMPLE_TEI
    csl = json.loads((tmp_path / "raw" / "grobid" / "references.csl.json").read_text())
    assert csl[0]["id"] == "ref-1"
    assert csl[1]["id"] == "ref-18"
    assert captured["url"] == "http://grobid.local:8070/api/processReferences"
    assert captured["data"] == {"includeRawCitations": "1", "consolidateCitations": "2"}


def test_grobid_extract_204_writes_empty_listbibl_and_empty_csl(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    class FakeResponse:
        status_code = 204
        text = ""

        def raise_for_status(self):
            raise AssertionError("204 should not call raise_for_status")

    monkeypatch.setattr(
        "librarian.extractors.grobid.httpx.post",
        lambda *args, **kwargs: FakeResponse(),
    )

    refs = grobid.extract(pdf, tmp_path, base_url="http://grobid.local:8070", timeout=30)

    assert refs == []
    csl = json.loads((tmp_path / "raw" / "grobid" / "references.csl.json").read_text())
    assert csl == []
