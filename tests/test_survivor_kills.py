"""Targeted tests for behaviours the mutation check showed were unpinned: whitespace trimming,
multi-line JSON, duplicate-citation handling, per-chunk grounding, the v2 batch default and the
recall matcher's three branches."""
import json

from reg_intel import extract, score, store


def _doc(tmp_path, text):
    db = store.connect(tmp_path / "t.db")
    db.execute(
        "INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) VALUES('RBI','u','t','h',?,0)",
        (text,),
    )
    return db


def _ob(cit):
    return {"actor": "bank", "modality": "must", "action": "a", "deadline": None, "citation": cit}


def test_verify_citation_ignores_surrounding_whitespace():
    assert extract.verify_citation("  abc  ", "abc") is True
    assert extract._norm("  a \n b  ") == "a b"


def test_parse_json_reads_multi_line_objects():
    assert extract.parse_json('lead {\n  "a": 1,\n  "b": [2]\n}\ntrail') == {"a": 1, "b": [2]}


def test_v1_skips_a_repeated_citation_but_keeps_later_ones(tmp_path):
    db = _doc(tmp_path, "alpha clause. beta clause.")
    out = json.dumps({"obligations": [_ob("alpha clause."), _ob("alpha clause."), _ob("beta clause.")]})
    res = extract.extract_document(db, 1, lambda p: out, "m")
    assert res == {"grounded": 2, "ungrounded": 0, "parse_errors": 0}
    assert [r[0] for r in db.execute("SELECT citation FROM obligation ORDER BY id")] == [
        "alpha clause.",
        "beta clause.",
    ]


def test_v1_grounds_a_citation_against_its_own_chunk_only(tmp_path):
    db = _doc(tmp_path, "CITE ME" + "x" * 13000)  # 'CITE ME' lives only in the first chunk
    outs = iter([json.dumps({"obligations": []}), json.dumps({"obligations": [_ob("CITE ME")]})])
    res = extract.extract_document(db, 1, lambda p: next(outs), "m")
    assert res == {"grounded": 0, "ungrounded": 1, "parse_errors": 0}


def test_v2_default_batch_size_is_forty_lines(tmp_path):
    lines = [f"Banks shall comply with requirement number {i:02d}." for i in range(35)]
    db = _doc(tmp_path, "\n".join(lines))
    prompts = []
    extract.extract_document_v2(db, 1, lambda p: prompts.append(p) or '{"obligations": []}', "m")
    assert len(prompts) == 1  # 35 lines fit one batch of 40 (a default of 30 would make two)


def test_match_uses_containment_either_way_and_the_threshold():
    assert score._match("a very long gold citation about deposits", "long gold citation") is True
    assert score._match("short", "a much longer extracted short clause") is True
    assert score._match("abcdefghij", "klmnopqrst") is False
    assert score._match("abcdefgxyz", "abcdefgpqr") is True  # ratio exactly 0.7
    assert score._match("abcdefxyzw", "abcdefpqrs") is False  # ratio 0.6
    assert score._match("", "abc") is False and score._match("abc", "") is False
