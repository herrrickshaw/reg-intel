"""Retry/backoff policy and env settings, with a fake client (no network)."""
from __future__ import annotations

import pytest

from reg_intel import llm


class _Err(Exception):
    def __init__(self, status_code, msg="boom"):
        super().__init__(msg)
        self.status_code = status_code


class _Reply:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


def _fake_create(outcomes):
    """Each call pops the next outcome: an Exception to raise or a string to return."""
    calls = []

    def create(**kw):
        calls.append(kw)
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return _Reply(o)

    create.calls = calls
    return create


def test_settings_from_env_defaults_and_overrides():
    assert llm.settings_from_env({"GROQ_API_KEY": "k"}) == (llm.DEFAULT_BASE_URL, "k", llm.DEFAULT_MODEL)
    env = {"REG_INTEL_BASE_URL": "http://localhost:1234/v1", "REG_INTEL_API_KEY": "x", "REG_INTEL_MODEL": "m"}
    assert llm.settings_from_env(env) == ("http://localhost:1234/v1", "x", "m")
    with pytest.raises(KeyError):
        llm.settings_from_env({})


def test_call_uses_json_mode_and_returns_content():
    create = _fake_create(['{"obligations": []}'])
    out = llm.make_llm(create, "m", sleep=lambda s: None)("hello")
    assert out == '{"obligations": []}'
    kw = create.calls[0]
    assert kw["model"] == "m" and kw["response_format"] == {"type": "json_object"}
    assert kw["messages"] == [{"role": "user", "content": "hello"}]


def test_rate_limit_backs_off_then_succeeds():
    slept = []
    create = _fake_create([_Err(429), _Err(413), "ok"])
    assert llm.make_llm(create, "m", sleep=slept.append)("p") == "ok"
    assert slept == [llm.BACKOFF_SECONDS, 2 * llm.BACKOFF_SECONDS]


def test_non_retryable_error_raises_immediately():
    slept = []
    create = _fake_create([_Err(500)])
    with pytest.raises(_Err):
        llm.make_llm(create, "m", sleep=slept.append)("p")
    assert slept == [] and len(create.calls) == 1


def test_gives_up_after_max_attempts():
    create = _fake_create([_Err(429)] * llm.MAX_ATTEMPTS)
    with pytest.raises(_Err):
        llm.make_llm(create, "m", sleep=lambda s: None)("p")
    assert len(create.calls) == llm.MAX_ATTEMPTS


def test_json_validate_failed_becomes_empty_output():
    create = _fake_create([_Err(400, "json_validate_failed: truncated")])
    assert llm.make_llm(create, "m", sleep=lambda s: None)("p") == ""
