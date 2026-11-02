"""Pure helpers in reg_intel.extract on small fixture text (no DB, no model)."""
from __future__ import annotations

import pytest

from reg_intel import extract


def test_chunks_overlap_and_cover_text():
    text = "abcdefghij" * 10  # 100 chars
    got = list(extract.chunks(text, size=40, overlap=10))
    assert [len(c) for c in got] == [40, 40, 40, 10]
    assert got[0][-10:] == got[1][:10]  # consecutive chunks share the overlap
    assert "".join(c[:30] for c in got[:3]) + got[3] == text


def test_parse_json_tolerates_fences_and_rejects_garbage():
    assert extract.parse_json('```json\n{"obligations": [1]}\n```') == {"obligations": [1]}
    assert extract.parse_obligations('{"x": 1}') == []
    with pytest.raises(ValueError):
        extract.parse_json("no json here")
    with pytest.raises(ValueError):
        extract.parse_obligations('{"obligations": [oops')


def test_doc_lines_drops_short_lines_and_strips():
    text = "  Heading\n\nBanks shall report fraud to the RBI within 14 days.  \nx\n" \
           "A second long enough line here."
    assert extract.doc_lines(text) == ["Banks shall report fraud to the RBI within 14 days.",
                                       "A second long enough line here."]
    assert extract.doc_lines(text, min_len=1) == ["Heading", "Banks shall report fraud to the RBI within 14 days.",
                                                  "x", "A second long enough line here."]


def test_numbered_batches_slices_in_order():
    text = "\n".join(f"line number {i} padded to be long enough" for i in range(5))
    batches = list(extract.numbered_batches(text, per_batch=2))
    assert [len(b) for b in batches] == [2, 2, 1]
    assert batches[0][0].startswith("line number 0")
    assert extract.v2_prompt(batches[2]).endswith("0: line number 4 padded to be long enough")


def test_marker_kind_and_lead_in_predicates():
    assert extract._marker_kind("(4) A bank shall") == "digit"
    assert extract._marker_kind("iii) something") == "roman"
    assert extract._marker_kind("(b) something") == "alpha"
    assert extract._marker_kind("i) single roman i") == "roman"
    assert extract._marker_kind("Plain sentence.") is None
    assert extract._is_leadin("subject to the following conditions:")
    assert extract._is_leadin("shall comply with the following.")
    assert not extract._is_leadin("Banks shall report fraud.")
    assert extract._is_item("(a) keep records;") and extract._is_item("keep records; and")
    assert not extract._is_item("Banks shall report fraud.")
