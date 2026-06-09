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


class _ChatResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_openai_provider_posts_chat_completion(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _ChatResponse('["Cryptocurrency", "Security"]')

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    config = {
        "classification": {
            "provider": "vllm",
            "model": "Qwen/Qwen3.6-35B-A3B-FP8",
            "api_base": "http://spark-f80b.local:8000/v1",
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
    }

    result = llm.complete("classify this", config, max_tokens=200)

    assert result == '["Cryptocurrency", "Security"]'
    assert captured["url"] == "http://spark-f80b.local:8000/v1/chat/completions"
    assert captured["payload"]["model"] == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "classify this"}]
    # extra_body merged into the request, thinking disabled
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}


class _ModelsResponse:
    def __init__(self, model_id):
        self._model_id = model_id

    def raise_for_status(self):
        pass

    def json(self):
        return {"object": "list", "data": [{"id": self._model_id, "object": "model"}]}


def test_openai_model_auto_discovers_served_model(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["models_url"] = url
        return _ModelsResponse("Qwen/Qwen3.6-35B-A3B-FP8")

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["sent_model"] = json["model"]
        return _ChatResponse('["a/b"]')

    import httpx
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)

    config = {
        "classification": {
            "provider": "vllm",
            "model": "auto",
            "api_base": "http://spark-f80b.local:8000/v1",
        }
    }

    assert llm.complete("p", config) == '["a/b"]'
    # discovered from /v1/models, then sent verbatim in the chat request
    assert captured["models_url"] == "http://spark-f80b.local:8000/v1/models"
    assert captured["sent_model"] == "Qwen/Qwen3.6-35B-A3B-FP8"


def test_openai_empty_content_retries_then_gives_up(monkeypatch):
    # A reasoning model that emits no content (thinking ate the budget) should
    # be retried, not silently returned as an answer.
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return _ChatResponse(None)

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(llm, "_RETRY_DELAY_S", 0)

    config = {"classification": {"provider": "openai"}}
    assert llm.complete("p", config) == ""
    assert len(calls) == 1 + llm._RETRIES
