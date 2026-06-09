"""Tests for the shared LLM client."""

import librarian.llm as llm


class _FakeResponse:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    def json(self):
        return {"response": self._text}


def test_ollama_completion(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json["model"], json["prompt"]))
        return _FakeResponse("an answer")

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    result = llm.complete("a prompt", {"classification": {"model": "llama3.2"}})

    assert result == "an answer"
    assert calls == [("http://localhost:11434/api/generate", "llama3.2", "a prompt")]


def test_ollama_url_override(monkeypatch):
    seen_urls = []

    def fake_post(url, json=None, timeout=None):
        seen_urls.append(url)
        return _FakeResponse("ok")

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    config = {"classification": {"ollama_url": "http://gpu-box:11434/"}}
    assert llm.complete("p", config) == "ok"
    assert seen_urls == ["http://gpu-box:11434/api/generate"]


def test_retries_then_succeeds(monkeypatch):
    attempts = []

    def flaky_post(url, json=None, timeout=None):
        attempts.append(1)
        if len(attempts) < 2:
            raise ConnectionError("transient")
        return _FakeResponse("recovered")

    import httpx
    monkeypatch.setattr(httpx, "post", flaky_post)
    monkeypatch.setattr(llm, "_RETRY_DELAY_S", 0)

    assert llm.complete("p", {}) == "recovered"
    assert len(attempts) == 2


def test_returns_empty_after_exhausted_retries(monkeypatch):
    def always_fail(url, json=None, timeout=None):
        raise ConnectionError("down")

    import httpx
    monkeypatch.setattr(httpx, "post", always_fail)
    monkeypatch.setattr(llm, "_RETRY_DELAY_S", 0)

    assert llm.complete("p", {}) == ""
