"""reg_intel.extract: boundary/default-argument pinning (line length, batching, numbering, lead-in depth/window)."""
import json

import pytest

from reg_intel import extract, store


def make_db(tmp_path, text):
    db = store.connect(tmp_path / "t.db")
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) VALUES('RBI','u','t','h',?,0)", (text,))
    db.commit()
    return db


def ob(i):
    return {"i": i, "actor": "a", "modality": "must", "action": "x", "deadline": None}


def test_doc_lines_min_len_boundary_is_inclusive_25():
    l25, l24 = "a" * 25, "b" * 24
    assert extract.doc_lines(f"{l25}\n{l24}") == [l25]
    assert extract.doc_lines(f"  {l25}  \n{l24}") == [l25]           # stripped before measuring
    assert extract.doc_lines(l24 + "\n" + "c" * 10, min_len=10) == [l24, "c" * 10]
    assert extract.doc_lines("c" * 10, min_len=11) == []


def test_numbered_batches_defaults_40_per_batch_and_min_len_25():
    text = "\n".join(f"{i:025d}" for i in range(41)) + "\n" + "z" * 24
    assert [len(b) for b in extract.numbered_batches(text)] == [40, 1]
    assert [len(b) for b in extract.numbered_batches(text, min_len=24)] == [40, 2]


def test_v1_parse_error_in_one_chunk_does_not_stop_later_chunks(tmp_path):
    first, second = "A" * 11500, "Banks shall report. " + "B" * 12000
    db = make_db(tmp_path, first + second)
    cit = "Banks shall report."
    outs = iter(["not json", json.dumps({"obligations": [{"actor": "b", "modality": "must", "action": "r", "deadline": None, "citation": cit}]}),
                 json.dumps({"obligations": []})])
    res = extract.extract_document(db, 1, lambda p: next(outs), "m")
    assert res == {"grounded": 1, "ungrounded": 0, "parse_errors": 1}


def test_v1_grounded_and_ungrounded_counted_separately(tmp_path):
    db = make_db(tmp_path, "Banks shall report fraud.")
    out = json.dumps({"obligations": [
        {"actor": "b", "modality": "must", "action": "r", "deadline": None, "citation": "Banks shall   report fraud."},
        {"actor": "b", "modality": "must", "action": "r", "deadline": None, "citation": "made up sentence"},
        {"actor": "b", "modality": "must", "action": "r", "deadline": None, "citation": ["Banks shall", "fraud."]}]})
    res = extract.extract_document(db, 1, lambda p: out, "m")
    assert res == {"grounded": 2, "ungrounded": 1, "parse_errors": 0}
    assert [r[0] for r in db.execute("SELECT grounded FROM obligation ORDER BY id")] == [1, 0, 1]
    assert db.execute("SELECT citation FROM obligation WHERE id=3").fetchone()[0] == json.dumps(["Banks shall", "fraud."])


def test_v2_prompt_is_zero_numbered_and_rows_are_grounded(tmp_path):
    lines = [f"Banks shall comply with item {i:02d} always." for i in range(3)]
    db = make_db(tmp_path, "\n".join(lines))
    prompts = []
    res = extract.extract_document_v2(db, 1, lambda p: prompts.append(p) or json.dumps({"obligations": [ob(0)]}), "m")
    assert res == {"selected": 1, "parse_errors": 0}
    assert prompts[0].startswith(extract.PROMPT_V2)
    assert prompts[0].endswith("\n".join(f"{n}: {l}" for n, l in enumerate(lines)))
    assert db.execute("SELECT grounded, prompt_ver, model, citation FROM obligation").fetchall() == [(1, "v2-lines", "m", lines[0])]


def test_v2_default_batch_size_is_40(tmp_path):
    lines = [f"Banks shall comply with item {i:03d} always." for i in range(41)]
    db = make_db(tmp_path, "\n".join(lines))
    prompts = []
    extract.extract_document_v2(db, 1, lambda p: prompts.append(p) or '{"obligations": []}', "m")
    assert len(prompts) == 2 and prompts[1].endswith("0: " + lines[40])


def test_v2_lead_in_computed_against_whole_document_not_batch(tmp_path):
    lines = ["The bank shall do the following:", "(a) first thing to do now;", "(b) second thing to do now;"]
    db = make_db(tmp_path, "\n".join(lines))
    outs = iter(['{"obligations": []}', json.dumps({"obligations": [ob(0)]})])   # batch 2 starts at line index 2
    extract.extract_document_v2(db, 1, lambda p: next(outs), "m", per_batch=2)
    assert db.execute("SELECT citation, lead_in FROM obligation").fetchall() == [(lines[2], lines[0])]


@pytest.mark.parametrize("line,kind", [("(i) text", "roman"), ("v) text", "roman"), ("(iv) text", "roman"),
                                       ("x) text", "alpha"), ("(c) text", "alpha"), ("(a) text", "alpha"),
                                       ("(4) text", "digit"), ("12) text", "digit"), ("plain text", None)])
def test_marker_kind(line, kind):
    assert extract._marker_kind(line) == kind


def test_is_leadin_following_rules():
    f = extract._is_leadin
    assert f("Banks must do the following.") and f("see the undernoted.") and f("Rule:") and f("Rule:-")
    assert not f("Banks must do the following things")           # no period, no colon
    assert not f("Ends with a period but no keyword.")
    assert f("the following " + "y" * 60 + ".")                  # keyword within last 80 chars
    assert not f("the following " + "y" * 80 + ".")              # keyword beyond last 80 chars


def test_find_lead_in_continuation_reaches_first_line():
    lines = ["alpha start of the sentence", "beta continues the sentence", "gamma continues the sentence"]
    assert extract.find_lead_in(lines, 2) == " ".join(lines[:2])


def test_find_lead_in_continuation_stops_at_lowercase_marker():
    lines = ["Head sentence here.", "a) lowercase marker line", "continues here"]
    assert extract.find_lead_in(lines, 2) == lines[1]


def test_find_lead_in_paragraph_between_item_and_lead_in_blocks_it():
    lines = ["Lead in:", "5. paragraph head without colon", "(a) item text goes here"]
    assert extract.find_lead_in(lines, 2) == ""


def test_find_lead_in_default_window_is_15_inclusive():
    lines = ["Far lead-in:"] + ["x" * 30] * 14 + ["(a) item that needs context;"]
    assert len(lines) - 1 == 15
    assert extract.find_lead_in(lines, 15) == "Far lead-in:"
    assert extract.find_lead_in(lines, 15, window=14) == ""


def test_find_lead_in_chain_is_limited_to_depth_two():
    lines = ["Top level rule:", "(1) second level:", "(a) third level:", "(i) fourth level:", "(A) leaf item text"]
    assert extract.find_lead_in(lines, 4) == "(1) second level: (a) third level: (i) fourth level:"
    assert extract.find_lead_in(lines, 4, depth=0) == "(i) fourth level:"


def test_find_lead_in_first_line_is_never_a_list_tail_of_last_line():
    # i == 0 must not look at lines[-1]
    lines = ["Unmarked opening sentence here.", "Banks shall comply with these:"]
    assert extract.find_lead_in(lines, 0) == ""
