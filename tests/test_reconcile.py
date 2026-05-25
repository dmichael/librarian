import json
from pathlib import Path

from librarian.reconcile import (
    apply_reconciliation,
    build_reconciliation_packet,
    call_openai_compatible_chat,
    reconcile_book,
    resolve_base_url,
)


def test_build_reconciliation_packet_uses_review_findings(tmp_path: Path):
    marker = tmp_path / "raw" / "marker"
    marker.mkdir(parents=True)
    (marker / "document.md").write_text(
        "K = K_o + B\\cos[\\phi(t) + \\phi_i]. \\tag{10}\n"
    )
    review = tmp_path / "review"
    review.mkdir()
    (review / "equation_diffs.json").write_text(
        json.dumps(
            [
                {
                    "number": "9",
                    "status": "ok",
                    "marker": "ok",
                    "pdftext": "ok",
                    "notes": [],
                },
                {
                    "number": "10",
                    "status": "review",
                    "marker": "phi_i",
                    "pdftext": "fj",
                    "notes": ["Possible phase-symbol disagreement."],
                },
            ]
        )
    )

    packet = build_reconciliation_packet(tmp_path)

    assert packet["domain"] == "equations"
    assert packet["marker_markdown_path"] == "raw/marker/document.md"
    assert len(packet["findings"]) == 1
    assert packet["findings"][0]["number"] == "10"
    assert packet["findings"][0]["raw_markdown_candidates"] == [
        "K = K_o + B\\cos[\\phi(t) + \\phi_i]. \\tag{10}"
    ]


def test_book2_fixture_packet_contains_exact_markdown_candidate():
    fixture = Path("tests/fixtures/reconcile/simple_motor_gestures_book2")

    packet = build_reconciliation_packet(fixture)

    assert packet["findings"][0]["raw_markdown_candidates"] == [
        r"K = K\_o + B\cos[\phi(t) + \phi\_i]. \tag{10}"
    ]


def test_apply_reconciliation_writes_clean_document_and_corrections(tmp_path: Path):
    marker = tmp_path / "raw" / "marker"
    marker.mkdir(parents=True)
    before = "K = K_o + B\\cos[\\phi(t) + \\phi_i]. \\tag{10}"
    after = "K = K_o + B\\cos[\\phi(t) + \\phi_j]. \\tag{10}"
    (marker / "document.md").write_text(before + "\n")

    applied = apply_reconciliation(
        tmp_path,
        {
            "patches": [
                {
                    "target_type": "equation",
                    "target_id": "10",
                    "operation": "replace",
                    "before": before,
                    "after": after,
                    "confidence": "high",
                    "rationale": "pdftotext shows fj.",
                    "evidence": ["equation_diffs.json"],
                }
            ]
        },
    )

    assert applied[0].applied
    assert (tmp_path / "clean" / "document.md").read_text() == after + "\n"
    corrections = json.loads((tmp_path / "clean" / "corrections.json").read_text())
    assert corrections[0]["target_id"] == "10"
    assert corrections[0]["applied"] is True


def test_resolve_base_url_uses_explicit_value(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://example.test/v1")

    assert resolve_base_url("http://spark-f80b.local:11434/v1") == (
        "http://spark-f80b.local:11434/v1"
    )


def test_resolve_base_url_uses_openai_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://spark-f80b.local:11434/v1")

    assert resolve_base_url(None) == "http://spark-f80b.local:11434/v1"


def test_resolve_base_url_requires_openai_base_url_convention(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://spark-f80b.local:11434")

    try:
        resolve_base_url(None)
    except ValueError as exc:
        assert "OPENAI_BASE_URL" in str(exc)
    else:
        raise AssertionError("Expected missing OPENAI_BASE_URL to fail")


def test_packet_only_does_not_need_base_url(tmp_path: Path):
    result = reconcile_book(
        tmp_path,
        base_url="",
        api_key="",
        model="qwen3:14b",
        timeout=1,
        json_mode="object",
        apply=False,
        packet_only=True,
    )

    assert result["packet"] == str(tmp_path / "review" / "reconciliation_packet.json")
    assert (tmp_path / "review" / "reconciliation_packet.json").exists()


def test_openai_compatible_chat_uses_chat_completions_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"patches":[]}'}}]}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("librarian.reconcile.httpx.post", fake_post)

    result = call_openai_compatible_chat(
        base_url="http://spark-f80b.local:11434/v1",
        api_key="ollama",
        model="qwen3:14b",
        messages=[{"role": "user", "content": "JSON"}],
        timeout=30,
        json_mode="object",
    )

    assert result == {"patches": []}
    assert captured["url"] == "http://spark-f80b.local:11434/v1/chat/completions"
    assert captured["json"]["model"] == "qwen3:14b"
    assert captured["json"]["response_format"] == {"type": "json_object"}


