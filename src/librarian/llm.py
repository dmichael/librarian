"""Single owner of LLM completion calls.

Every module that needs a text completion (classification, RAG synthesis,
chapter summaries) goes through complete(), so provider/model resolution,
endpoints, timeouts, retries, and error policy live in one place.

Provider and model come from the `classification` config block:

    classification:
      provider: ollama | anthropic | openai   # "vllm" is an alias for "openai"
      model: llama3.2 | claude-... | Qwen/...
      ollama_url: http://localhost:11434      # ollama only
      api_base: http://host:8000/v1           # openai-compatible only
      api_key: ...                            # openai-compatible; defaults to env/EMPTY
      extra_body:                             # openai-compatible passthrough, e.g.
        chat_template_kwargs:                 # disable Qwen3 thinking so the
          enable_thinking: false              # answer lands in message.content
"""

import logging
import os
import time

log = logging.getLogger(__name__)

DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "llama3.2"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OPENAI_BASE = "http://localhost:8000/v1"

_RETRIES = 2  # total attempts = 1 + _RETRIES
_RETRY_DELAY_S = 2.0


def complete(
    prompt: str,
    config: dict,
    max_tokens: int = 1024,
    timeout: float = 120.0,
) -> str:
    """Run a completion against the configured provider.

    Returns the completion text, or "" after logging if all attempts fail —
    callers treat an empty string as "no answer" (matching the historical
    behavior of the per-module clients).
    """
    llm_config = config.get("classification", {})
    provider = llm_config.get("provider", DEFAULT_PROVIDER)
    model = llm_config.get("model", DEFAULT_MODEL)

    last_error = None
    for attempt in range(1 + _RETRIES):
        if attempt:
            time.sleep(_RETRY_DELAY_S)
        try:
            if provider == "anthropic":
                return _complete_anthropic(prompt, model, max_tokens)
            if provider in ("openai", "vllm"):
                return _complete_openai(prompt, model, llm_config, max_tokens, timeout)
            return _complete_ollama(prompt, model, llm_config, timeout)
        except Exception as e:
            last_error = e
            log.warning(
                "LLM call failed (%s/%s, attempt %d/%d): %s",
                provider, model, attempt + 1, 1 + _RETRIES, e,
            )

    log.error("LLM call failed after %d attempts (%s/%s): %s",
              1 + _RETRIES, provider, model, last_error)
    return ""


def _complete_ollama(prompt: str, model: str, llm_config: dict, timeout: float) -> str:
    import httpx

    base_url = llm_config.get("ollama_url", DEFAULT_OLLAMA_URL).rstrip("/")
    response = httpx.post(
        f"{base_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("response", "")


def _complete_openai(
    prompt: str, model: str, llm_config: dict, max_tokens: int, timeout: float
) -> str:
    """Call an OpenAI-compatible chat endpoint (e.g. vLLM on the Spark)."""
    import httpx

    base_url = llm_config.get("api_base", DEFAULT_OPENAI_BASE).rstrip("/")
    api_key = llm_config.get("api_key") or os.environ.get("OPENAI_API_KEY", "EMPTY")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": llm_config.get("temperature", 0),
    }
    # Server-specific passthrough (e.g. chat_template_kwargs to disable Qwen3
    # thinking, which otherwise consumes the token budget as reasoning and
    # leaves message.content empty).
    extra_body = llm_config.get("extra_body")
    if extra_body:
        payload.update(extra_body)

    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    content = message.get("content")
    if not content:
        # Empty content with a reasoning model usually means the answer was
        # truncated by thinking — raise so the retry loop re-attempts.
        raise ValueError("empty completion content (reasoning may be enabled)")
    return content


def _complete_anthropic(prompt: str, model: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
