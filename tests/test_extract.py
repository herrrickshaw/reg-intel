"""reg_intel.extract: chunking, JSON parsing, line batches, lead-in context and v1/v2 extraction.
The LLM is always a local lambda; no API key or network is used."""
import json

import pytest

from reg_intel import extract, store


def test_chunks_overlap_and_cover_text():
    text = "".join(chr(65 + i % 26) for i in range(30000))
    cs = list(extract.chunks(text, size=12000, overlap=500))
    assert [len(c) for c in cs] == [12000, 12000, 7000]
    assert cs[1][:500] == cs[0][-500:]                      # overlap
    assert cs[0] + cs[1][500:] + cs[2][500:] == text        # nothing lost


def test_chunks_empty():
    assert list(extract.chunks("")) == []


def test_parse_json_fenced_and_errors():
    assert extract.parse_json('```json\n{"obligations": []}\n```') == {"obligations": []}
    with pytest.raises(ValueError):
        extract.parse_json("no json here")
    with pytest.raises(ValueError):
        extract.parse_json('{"obligations": [oops}')


def test_verify_citation_rejects_non_strings():
    assert not extract.verify_citation(None, "x")
    assert not extract.verify_citation(42, "42")
    assert not extract.verify_citation([], "x")


def test_doc_lines_and_batches():
    text = "short\n" + "\n".join(f"Line number {i:02d} that is long enough" for i in range(5))
    assert len(extract.doc_lines(text)) == 5
    assert [len(b) for b in extract.numbered_batches(text, per_batch=2)] == [2, 2, 1]


@pytest.mark.parametrize("line,kind", [
    ("(4) A bank shall", "digit"), ("4) A bank", "digit"),
    ("ii) Claims", "roman"), ("(iii) x", "roman"), ("(i) x", "roman"), ("v) x", "roman"),
    ("(a) x", "alpha"), ("b) x", "alpha"), ("(c) x", "alpha"),
    ("A bank shall", None), ("4. A bank", None),
])
def test_marker_kind(line, kind):
    assert extract._marker_kind(line) == kind


def test_find_lead_in_alpha_list_and_following():
    L = ["8. The bank shall ensure the following.",
         "(a) a Board-approved policy is in place;",
         "(b) staff are trained on the policy."]
    assert extract.find_lead_in(L, 1) == L[0]
    assert extract.find_lead_in(L, 2) == L[0]           # sibling (a) skipped


def test_find_lead_in_window_limit():
    L = ["Lead in line:"] + [f"filler line number {i} without punctuation" for i in range(20)] + ["(a) item far away;"]
    assert extract.find_lead_in(L, len(L) - 1, window=5) == ""


def _doc(db, text):
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text) VALUES('RBI','u','t','h',?)", (text,))
    return 1


def test_extract_v1_overlap_does_not_double_count(tmp_path):
    """An obligation that falls in the 500-char overlap between two chunks is seen by both LLM calls.
    It is one obligation and must be stored (and counted as grounded) once."""
    sentence = "Banks shall report every fraud to the RBI within 14 days of detection."
    pad = "x " * 5900                                  # ~11,800 chars, so the sentence sits in the overlap
    text = pad + sentence + " " + "y " * 3000
    assert sentence in text[:12000] and sentence in text[11500:]
    db = store.connect(tmp_path / "t.db")
    _doc(db, text)

    def llm(prompt):
        obs = [{"actor": "Banks", "modality": "must", "action": "report fraud", "deadline": "14 days",
                "citation": sentence}] if sentence in prompt else []
        return json.dumps({"obligations": obs})

    res = extract.extract_document(db, 1, llm, "fake")
    assert res == {"grounded": 1, "ungrounded": 0, "parse_errors": 0}
    assert db.execute("SELECT count(*) FROM obligation").fetchone()[0] == 1
    assert store.verify_chain(db)


def test_extract_v1_list_citation_stored_as_json(tmp_path):
    db = store.connect(tmp_path / "t.db")
    _doc(db, "Banks shall report fraud. Banks must comply.")
    out = json.dumps({"obligations": [{"citation": ["Banks shall report fraud.", "Banks must comply."]}]})
    assert extract.extract_document(db, 1, lambda p: out, "fake")["grounded"] == 1
    assert json.loads(db.execute("SELECT citation FROM obligation").fetchone()[0]) == ["Banks shall report fraud.", "Banks must comply."]


def test_extract_v2_batches_map_back_to_global_lines(tmp_path):
    lines = [f"{i}. Banks shall comply with requirement number {i} at all times." for i in range(1, 6)]
    db = store.connect(tmp_path / "t.db")
    _doc(db, "\n".join(lines))
    # every batch selects its last line; batch size 2 -> lines 2, 4, 5
    res = extract.extract_document_v2(db, 1, lambda p: json.dumps({"obligations": [{"i": p.count("\n") - extract.PROMPT_V2.count("\n")}]}),
                                      "fake", per_batch=2)
    got = [r[0] for r in db.execute("SELECT citation FROM obligation ORDER BY id")]
    assert res == {"selected": 3, "parse_errors": 0}
    assert got == [lines[1], lines[3], lines[4]]


def test_extract_v2_rejects_string_and_negative_index(tmp_path):
    db = store.connect(tmp_path / "t.db")
    _doc(db, "Banks shall comply with this requirement at all times.")
    out = json.dumps({"obligations": [{"i": -1}, {"i": "0"}, {"i": 0}]})
    assert extract.extract_document_v2(db, 1, lambda p: out, "fake")["selected"] == 1


def test_backfill_lead_in(tmp_path):
    lines = ["15. Corporate agency shall be subject to the following conditions:",
             "There shall be a Board approved policy for distribution;"]
    db = store.connect(tmp_path / "t.db")
    _doc(db, "\n".join(lines))
    db.execute("INSERT INTO obligation(doc_id,citation,grounded,prompt_ver,lead_in) VALUES(1,?,1,'v2-lines','')", (lines[1],))
    assert extract.backfill_lead_in(db) == 1
    assert db.execute("SELECT lead_in FROM obligation").fetchone()[0] == lines[0]


def test_openai_llm_needs_a_key(monkeypatch):
    pytest.importorskip("openai")
    for k in ("REG_INTEL_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(KeyError):
        extract.openai_compatible_llm()


def test_openai_llm_returns_empty_string_when_content_is_none(monkeypatch):
    """A JSON-mode reply with ``content=None`` must surface as "" (the caller's
    parse-error path), not as ``None`` -- the ``Callable[[str], str]`` contract."""
    openai = pytest.importorskip("openai")

    class _Msg:
        content = None

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        def __init__(self, api_key, base_url):
            self.chat = _Chat()

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    monkeypatch.setenv("REG_INTEL_API_KEY", "test-key-not-real")
    call, _model = extract.openai_compatible_llm()
    out = call("prompt")
    assert out == ""
    # and the extractor logs it as a parse error instead of crashing on None
    with pytest.raises(ValueError):
        extract.parse_json(out)
