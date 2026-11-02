"""Gold scoring and precision sheets on tiny fixtures (no network, no model)."""
from __future__ import annotations

import json

import pytest

from reg_intel import score, store

SRC = "Banks shall report fraud to the RBI within 14 days.\nBanks may outsource printing.\nDefinitions follow."
URL = "https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=777"
GOLD = {"url": URL, "title": "T", "obligations": [
    {"citation": "Banks shall report fraud to the RBI within 14 days."},
    {"citation": "Banks may outsource printing."}]}


def _db(tmp_path):
    db = store.connect(tmp_path / "t.db")
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text) VALUES('RBI',?,'T','h',?)", (URL, SRC))
    return db


def test_match_containment_and_fuzzy():
    assert score._match("Banks shall report fraud", "banks  shall report fraud to the RBI")
    assert score._match("Banks shall report fraud to RBI in 14 days", "Banks shall report fraud to the RBI in 14 days")
    assert not score._match("Banks shall report fraud", "Insurers may settle claims")
    assert not score._match("", "x")


def test_load_gold_and_gold_key(tmp_path):
    (tmp_path / "rbi_777_demo.json").write_text(json.dumps(GOLD), encoding="utf-8")
    pattern = str(tmp_path / "*.json")
    assert score.load_gold(pattern) == [GOLD]
    assert score.gold_key(GOLD, pattern) == "rbi_777_demo"
    with pytest.raises(LookupError):
        score.gold_key({"url": "https://x?id=1"}, pattern)


def test_validate_flags_bad_labels_and_missing_document(tmp_path):
    db = _db(tmp_path)
    assert score.validate(db, GOLD) == []
    bad = {"url": URL, "obligations": [{"citation": "invented text"}]}
    assert score.validate(db, bad) == ["invented text"]
    assert score.validate(db, {"url": "https://x?id=0", "obligations": []}) == ["document not in store: https://x?id=0"]


def test_score_recall_uses_only_grounded_rows(tmp_path):
    db = _db(tmp_path)
    rows = [("Banks shall report fraud to the RBI within 14 days.", 1, "v1"),
            ("Banks may outsource printing.", 0, "v1"),  # ungrounded: must not count
            ("Definitions follow.", 1, "v2-lines")]
    db.executemany("INSERT INTO obligation(doc_id,citation,grounded,prompt_ver) VALUES(1,?,?,?)", rows)
    r = score.score(db, GOLD)
    assert (r["gold"], r["recall"], r["extracted"], r["grounded_share"]) == (2, 0.5, 3, 0.67)
    assert r["missed"] == ["Banks may outsource printing."]
    assert score.score(db, GOLD, prompt_ver="v2-lines")["recall"] == 0.0


def test_export_review_dedups_and_keeps_verdicts(tmp_path, monkeypatch):
    monkeypatch.setattr(score, "PRECISION_DIR", str(tmp_path / "prec"))
    db = _db(tmp_path)
    db.executemany("INSERT INTO obligation(doc_id,citation,modality,lead_in,grounded,prompt_ver) VALUES(1,?,?,?,1,?)",
                   [("Banks may outsource printing.", "may", "", "v2-lines"),
                    ("Banks may outsource printing.", "may", "", "v2-lines"),
                    ("Definitions follow.", "must", "Lead:", "v2-lines"),
                    ("Banks shall report fraud to the RBI within 14 days.", "must", "", "v1")])
    path = score.export_review(db, GOLD, "k", "v2-lines")
    rows = score._read_rows(path)
    assert [r["citation"] for r in rows] == ["Banks may outsource printing.", "Definitions follow."]
    assert rows[1]["lead_in"] == "Lead:"
    rows[0]["human"] = "y"
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=score.COLS)
        w.writeheader()
        w.writerows(rows)
    score.export_review(db, GOLD, "k", "v2-lines")  # re-export preserves the human verdict
    assert score._read_rows(path)[0]["human"] == "y"
    assert score.precision("k") == {"rows": 2, "judged": 1, "human_reviewed": 1,
                                    "strict": 1.0, "lenient": 1.0, "y": 1, "f": 0, "n": 0}
    assert score.precision("missing") is None
