"""OpenAI-compatible chat client behind a tiny callable interface.

Extraction code depends only on the ``LLM`` protocol (``prompt -> text``) so it can be
driven by a fake in tests; ``openai_compatible_llm`` is the one place that touches the
network (Groq by default, any OpenAI-compatible endpoint via ``REG_INTEL_*`` variables).
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 20.0  # multiplied by the attempt number (20, 40, 60 s)
RETRYABLE_STATUS = (413, 429)  # payload too large / rate limited on free tiers
MAX_TOKENS = 5000
TEMPERATURE = 0
JSON_VALIDATE_FAILED = "json_validate_failed"  # Groq 400 when a reasoning model runs out of tokens


class LLM(Protocol):
    """Anything that turns a prompt into the model's raw text reply."""

    def __call__(self, prompt: str) -> str: ...


def settings_from_env(env: Mapping[str, str] = os.environ) -> tuple[str, str, str]:
    """(base_url, api_key, model) from REG_INTEL_BASE_URL / REG_INTEL_API_KEY / REG_INTEL_MODEL.

    Falls back to Groq defaults; the key falls back to GROQ_API_KEY and raises KeyError if neither is set.
    """
    base = env.get("REG_INTEL_BASE_URL", DEFAULT_BASE_URL)
    key = env.get("REG_INTEL_API_KEY") or env["GROQ_API_KEY"]
    model = env.get("REG_INTEL_MODEL", DEFAULT_MODEL)
    return base, key, model


def make_llm(create: Callable[..., Any], model: str,
             sleep: Callable[[float], None] = time.sleep) -> LLM:
    """Wrap a ``chat.completions.create``-shaped callable with JSON mode and rate-limit backoff.

    ``create`` and ``sleep`` are injected so the retry policy is testable without a network.
    """

    def call(prompt: str) -> str:
        for attempt in range(MAX_ATTEMPTS):
            try:
                r = create(
                    model=model, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                    response_format={"type": "json_object"},
                    messages=[{"role": "user", "content": prompt}])
                return r.choices[0].message.content
            except Exception as e:  # noqa: BLE001 - provider SDKs raise many unrelated classes
                status = getattr(e, "status_code", None)
                if status == 400 and JSON_VALIDATE_FAILED in str(e):
                    log.warning("model returned invalid JSON (%s); treating as empty output", JSON_VALIDATE_FAILED)
                    return ""  # caller logs a parse error
                if attempt == MAX_ATTEMPTS - 1 or status not in RETRYABLE_STATUS:
                    raise
                wait = BACKOFF_SECONDS * (attempt + 1)
                log.warning("status %s on attempt %d/%d; retrying in %.0fs", status, attempt + 1, MAX_ATTEMPTS, wait)
                sleep(wait)
        raise RuntimeError("unreachable: retry loop exited without returning or raising")

    return call


def openai_compatible_llm() -> tuple[LLM, str]:
    """Groq by default (GROQ_API_KEY); override with REG_INTEL_BASE_URL/KEY/MODEL. Returns (llm, model)."""
    from openai import OpenAI  # imported lazily so offline tests never need the SDK

    base, key, model = settings_from_env()
    client = OpenAI(api_key=key, base_url=base)
    return make_llm(client.chat.completions.create, model), model
