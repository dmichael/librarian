import json
from pathlib import Path

from librarian.references import (
    call_grobid_process_references,
    resolve_grobid_base_url,
    tei_references_to_csl,
    write_references_artifacts,
)


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


def test_tei_references_to_csl_projects_grobid_tei_to_csl_json():
    refs = tei_references_to_csl(SAMPLE_TEI)

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


def test_write_references_artifacts_writes_raw_tei_and_clean_csl_json(tmp_path: Path):
    result = write_references_artifacts(tmp_path, SAMPLE_TEI)

    assert result.count == 2
    assert (tmp_path / "raw" / "grobid" / "references.tei.xml").read_text() == SAMPLE_TEI
    csl = json.loads((tmp_path / "clean" / "references.csl.json").read_text())
    assert csl[0]["id"] == "ref-1"
    assert csl[1]["id"] == "ref-18"


def test_call_grobid_process_references_uses_focused_endpoint(tmp_path: Path, monkeypatch):
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
        captured["files"] = files
        captured["data"] = data
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("librarian.references.httpx.post", fake_post)

    tei = call_grobid_process_references(
        pdf,
        grobid_base_url="http://grobid.local:8070",
        timeout=30,
        consolidate_citations="2",
    )

    assert tei == SAMPLE_TEI
    assert captured["url"] == "http://grobid.local:8070/api/processReferences"
    assert captured["headers"] == {"Accept": "application/xml"}
    assert captured["data"] == {
        "includeRawCitations": "1",
        "consolidateCitations": "2",
    }
    assert captured["timeout"] == 30


def test_call_grobid_process_references_204_means_empty_reference_list(
    tmp_path: Path, monkeypatch
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    class FakeResponse:
        status_code = 204
        text = ""

        def raise_for_status(self):
            raise AssertionError("204 should not call raise_for_status")

    monkeypatch.setattr("librarian.references.httpx.post", lambda *args, **kwargs: FakeResponse())

    tei = call_grobid_process_references(
        pdf,
        grobid_base_url="http://grobid.local:8070",
        timeout=30,
        consolidate_citations="0",
    )
    result = write_references_artifacts(tmp_path, tei)

    assert result.count == 0
    assert json.loads((tmp_path / "clean" / "references.csl.json").read_text()) == []


def test_resolve_grobid_base_url_requires_single_convention(monkeypatch):
    monkeypatch.delenv("GROBID_BASE_URL", raising=False)

    try:
        resolve_grobid_base_url(None)
    except ValueError as exc:
        assert "GROBID_BASE_URL" in str(exc)
    else:
        raise AssertionError("Expected missing GROBID_BASE_URL to fail")


def test_resolve_grobid_base_url_accepts_explicit_or_env(monkeypatch):
    monkeypatch.setenv("GROBID_BASE_URL", "http://env-grobid:8070/")

    assert resolve_grobid_base_url(None) == "http://env-grobid:8070"
    assert resolve_grobid_base_url("http://explicit-grobid:8070/") == (
        "http://explicit-grobid:8070"
    )
