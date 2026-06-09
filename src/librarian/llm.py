"""Single owner of LLM completion calls.

Every module that needs a text completion (classification, RAG synthesis,
chapter summaries) goes through complete(), so provider/model resolution,
endpoints, timeouts, retries, and error policy live in one place.

Provider and model come from the `classification` config block:

    classification:
      provider: ollama | anthropic
      model: llama3.2 | claude-...
      ollama_url: http://localhost:11434   # optional
"""

import logging
import os
import time

log = logging.getLogger(__name__)

DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "llama3.2"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

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


def _complete_anthropic(prompt: str, model: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
