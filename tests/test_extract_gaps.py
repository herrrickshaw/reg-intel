"""reg_intel.extract gaps: openai_compatible_llm retry/limit logic (fake openai), v2 parse errors, lead-in edges."""
import sys
import types

import pytest

from reg_intel import extract, store


class APIError(Exception):
    def __init__(self, status, msg="boom"):
        super().__init__(msg)
        self.status_code = status


@pytest.fixture
def fake_openai(monkeypatch):
    """Install a fake `openai` module; returns state with scripted create() outcomes."""
    st = types.SimpleNamespace(outcomes=[], kwargs=None, init=None, sleeps=[])

    def create(**kw):
        st.kwargs = kw
        o = st.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=o))])

    class OpenAI:
        def __init__(self, api_key, base_url):
            st.init = (api_key, base_url)
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

    mod = types.ModuleType("openai")
    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    monkeypatch.setattr("time.sleep", lambda s: st.sleeps.append(s))
    for k in ("REG_INTEL_BASE_URL", "REG_INTEL_API_KEY", "REG_INTEL_MODEL", "GROQ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return st


def test_llm_defaults_and_request_shape(fake_openai, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    fake_openai.outcomes = ['{"obligations": []}']
    call, model = extract.openai_compatible_llm()
    assert model == "openai/gpt-oss-120b"
    assert fake_openai.init == ("gk", "https://api.groq.com/openai/v1")
    assert call("hello") == '{"obligations": []}'
    kw = fake_openai.kwargs
    assert kw["model"] == model and kw["temperature"] == 0 and kw["max_tokens"] == 5000
    assert kw["response_format"] == {"type": "json_object"}
    assert kw["messages"] == [{"role": "user", "content": "hello"}]


def test_llm_env_overrides_and_key_precedence(fake_openai, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("REG_INTEL_API_KEY", "rk")
    monkeypatch.setenv("REG_INTEL_BASE_URL", "http://local/v1")
    monkeypatch.setenv("REG_INTEL_MODEL", "m-x")
    _, model = extract.openai_compatible_llm()
    assert model == "m-x" and fake_openai.init == ("rk", "http://local/v1")


def test_llm_missing_key_raises(fake_openai):
    with pytest.raises(KeyError):
        extract.openai_compatible_llm()


def test_llm_retries_413_429_with_backoff_then_succeeds(fake_openai, monkeypatch):
    monkeypatch.setenv("REG_INTEL_API_KEY", "k")
    fake_openai.outcomes = [APIError(429), APIError(413), "ok"]
    call, _ = extract.openai_compatible_llm()
    assert call("p") == "ok"
    assert fake_openai.sleeps == [20, 40]


def test_llm_gives_up_after_four_attempts(fake_openai, monkeypatch):
    monkeypatch.setenv("REG_INTEL_API_KEY", "k")
    fake_openai.outcomes = [APIError(429, f"e{i}") for i in range(4)] + ["never"]
    call, _ = extract.openai_compatible_llm()
    with pytest.raises(APIError, match="e3"):
        call("p")
    assert fake_openai.sleeps == [20, 40, 60]
    assert fake_openai.outcomes == ["never"]                  # exactly 4 attempts


def test_llm_non_retryable_status_raises_immediately(fake_openai, monkeypatch):
    monkeypatch.setenv("REG_INTEL_API_KEY", "k")
    fake_openai.outcomes = [APIError(500), "never"]
    call, _ = extract.openai_compatible_llm()
    with pytest.raises(APIError):
        call("p")
    assert fake_openai.sleeps == []
    # exception without a status_code at all is also re-raised
    fake_openai.outcomes = [RuntimeError("x")]
    with pytest.raises(RuntimeError):
        call("p")


def test_llm_400_json_validate_failed_returns_empty(fake_openai, monkeypatch):
    monkeypatch.setenv("REG_INTEL_API_KEY", "k")
    fake_openai.outcomes = [APIError(400, "json_validate_failed: out of tokens")]
    call, _ = extract.openai_compatible_llm()
    assert call("p") == ""
    fake_openai.outcomes = [APIError(400, "some other bad request")]
    with pytest.raises(APIError):                             # 400 without the marker is not swallowed
        call("p")


def test_v2_parse_error_is_logged_counted_and_skipped(tmp_path):
    db = store.connect(tmp_path / "t.db")
    lines = [f"Banks shall comply with requirement number {i:02d}." for i in range(5)]
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) VALUES('RBI','u','t','h',?,0)",
               ("\n".join(lines),))
    outs = iter(["garbage", '{"obligations": [{"i": 1, "actor": "bank", "modality": "must", "action": "a", "deadline": null}]}'])
    res = extract.extract_document_v2(db, 1, lambda p: next(outs), "m", per_batch=3)
    assert res == {"selected": 1, "parse_errors": 1}
    steps = [r[0] for r in db.execute("SELECT step FROM prov_event ORDER BY id")]
    assert steps == ["extract_v2", "extract_parse_error", "extract_v2"]
    # second batch starts at line 3, so i=1 -> line 4
    assert db.execute("SELECT citation FROM obligation").fetchall() == [(lines[4],)]
    assert store.verify_chain(db)


def test_v2_ignores_bad_indices(tmp_path):
    db = store.connect(tmp_path / "t.db")
    lines = [f"Banks shall comply with requirement number {i:02d}." for i in range(3)]
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) VALUES('RBI','u','t','h',?,0)",
               ("\n".join(lines),))
    obs = ',"actor":"a","modality":"must","action":"x","deadline":null}'
    out = '{"obligations":[' + ",".join('{"i":%s%s' % (i, obs) for i in ('3', '-1', '"1"', 'null', '2', '0')) + ']}'
    res = extract.extract_document_v2(db, 1, lambda p: out, "m")
    assert res == {"selected": 2, "parse_errors": 0}          # 0 and 2 valid; len(batch)=3 is out of range
    assert [r[0] for r in db.execute("SELECT citation FROM obligation ORDER BY id")] == [lines[2], lines[0]]


def test_find_lead_in_multi_line_lowercase_continuation():
    lines = ["Banks shall ensure that the customer is informed", "of the charges levied on the account",
             "and the manner of their recovery by the bank."]
    assert extract.find_lead_in(lines, 2) == " ".join(lines[0:2])
    assert extract.find_lead_in(lines, 1) == lines[0]


def test_find_lead_in_continuation_stops_at_marker_line():
    lines = ["Intro sentence for the clause below.", "(a) first limb of the sentence", "continues here quietly now"]
    assert extract.find_lead_in(lines, 2) == lines[1]


def test_find_lead_in_item_stops_at_numbered_paragraph():
    lines = ["5. Heading with no colon at the end of it", "(a) first item without any lead-in above",
             "(b) second item without any lead-in above"]
    assert extract.find_lead_in(lines, 2) == ""               # reaches '5.' paragraph before any lead-in
    assert extract.find_lead_in(lines, 1) == ""


def test_find_lead_in_numbered_paragraph_and_plain_line():
    lines = ["Lead-in text for list:", "3. A numbered paragraph that is self-contained.", "Plain sentence starts uppercase."]
    assert extract.find_lead_in(lines, 1) == ""
    assert extract.find_lead_in(lines, 2) == ""               # unmarked, not list-ish, not after list line


def test_find_lead_in_item_takes_nearest_lead_in_and_window():
    lines = ["Far lead-in:"] + ["(a) filler item number one here;"] * 0 + ["x" * 30] * 15 + ["(a) item that needs context;"]
    # lead-in is 16 lines back; scan covers i-1 .. i-window inclusive
    i = len(lines) - 1
    assert i == 16
    assert extract.find_lead_in(lines, i) == ""
    assert extract.find_lead_in(lines, i, window=16) == "Far lead-in:"


def test_find_lead_in_tail_of_list_and_depth_zero():
    lines = ["The bank shall:", "(a) do the first thing;", "Unmarked tail of the list continues here."]
    assert extract.find_lead_in(lines, 2) == "The bank shall:"
    assert extract.find_lead_in(lines, 2, depth=0) == ""      # tail_of_list needs depth > 0


def test_find_lead_in_nested_lead_in_gets_its_own():
    lines = ["Banks shall:", "(1) in the following cases:", "(a) case one applies."]
    assert extract.find_lead_in(lines, 2) == "Banks shall: (1) in the following cases:"


def test_backfill_lead_in_updates_only_matching_rows(tmp_path):
    db = store.connect(tmp_path / "t.db")
    text = "The bank shall do the following:\n(a) do the first thing now;"
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) VALUES('RBI','u','t','h',?,0)", (text,))
    db.executemany("INSERT INTO obligation(doc_id,citation,grounded,prompt_ver) VALUES(1,?,1,?)",
                   [("(a) do the first thing now;", "v2-lines"), ("not a line", "v2-lines"), ("(a) do the first thing now;", "v1")])
    db.commit()
    assert extract.backfill_lead_in(db) == 1
    assert [r[0] for r in db.execute("SELECT lead_in FROM obligation ORDER BY id")] == ["The bank shall do the following:", "", ""]
