"""reg_intel.score: citation matching, gold validation, recall scoring and review-sheet round trip."""
import csv

import pytest

from reg_intel import score, store

SRC = "Banks shall report fraud to the RBI within 14 days.\nBanks shall not levy charges on basic accounts."
GOLD = {"url": "u", "title": "t", "obligations": [
    {"citation": "Banks shall report fraud to the RBI within 14 days."},
    {"citation": "Banks shall not levy charges on basic accounts."}]}


@pytest.mark.parametrize("a,b,ok", [
    ("Banks shall report fraud.", "banks  shall report fraud.", True),          # case/whitespace
    ("Banks shall report fraud to the RBI", "Banks shall report fraud to the RBI within 14 days.", True),  # containment
    ("Banks shall report fraud to the RBI within 14 days.", "Banks shall report frauds to RBI within 14 days.", True),  # fuzzy
    ("Banks shall report fraud.", "Insurers may appoint agents.", False),
    ("", "anything", False),
])
def test_match(a, b, ok):
    assert score._match(a, b) is ok


@pytest.fixture
def db(tmp_path):
    db = store.connect(tmp_path / "t.db")
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text) VALUES('RBI','u','t','h',?)", (SRC,))
    return db


def test_validate(db):
    assert score.validate(db, GOLD) == []
    bad = {"url": "u", "obligations": [{"citation": "Banks may do anything."}]}
    assert score.validate(db, bad) == ["Banks may do anything."]
    assert score.validate(db, {"url": "missing", "obligations": []}) == ["document not in store: missing"]


def test_score_counts_only_grounded(db):
    db.executemany("INSERT INTO obligation(doc_id,citation,grounded,prompt_ver) VALUES(1,?,?,?)", [
        ("Banks shall report fraud to the RBI within 14 days.", 1, "v1"),
        ("Banks shall not levy charges on basic accounts.", 0, "v1"),     # ungrounded: does not count
        ("Banks shall not levy charges on basic accounts.", 1, "v2-lines")])
    r = score.score(db, GOLD, "v1")
    assert (r["gold"], r["recall"], r["extracted"], r["grounded_share"]) == (2, 0.5, 2, 0.5)
    assert r["missed"] == ["Banks shall not levy charges on basic accounts."]
    assert score.score(db, GOLD)["recall"] == 1.0


def test_export_review_preserves_verdicts(db, tmp_path, monkeypatch):
    monkeypatch.setattr(score, "PRECISION_DIR", str(tmp_path / "prec"))
    db.executemany("INSERT INTO obligation(doc_id,citation,modality,grounded,prompt_ver,lead_in) VALUES(1,?,?,1,'v2-lines',?)", [
        ("Banks shall report fraud to the RBI within 14 days.", "must", ""),
        ("Banks shall report fraud to the RBI within 14 days.", "must", ""),   # duplicate citation -> one row
        ("Banks shall not levy charges on basic accounts.", "must_not", "")])
    p = score.export_review(db, GOLD, "k", "v2-lines")
    rows = list(csv.DictReader(open(p)))
    assert len(rows) == 2
    rows[0]["human"] = "y"; rows[1]["claude_draft"] = "n"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=score.COLS); w.writeheader(); w.writerows(rows)
    score.export_review(db, GOLD, "k", "v2-lines")
    p2 = score.precision("k")
    assert (p2["judged"], p2["human_reviewed"], p2["y"], p2["n"], p2["strict"]) == (2, 1, 1, 1, 0.5)


def test_precision_missing_and_unjudged(tmp_path, monkeypatch):
    monkeypatch.setattr(score, "PRECISION_DIR", str(tmp_path))
    assert score.precision("nope") is None
    with open(tmp_path / "e.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=score.COLS); w.writeheader()
        w.writerow({"citation": "a", "lead_in": "", "modality": "must", "claude_draft": "", "human": "", "note": ""})
    assert score.precision("e") == {"rows": 1, "judged": 0}
