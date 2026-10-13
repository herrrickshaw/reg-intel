import json

from reg_intel import extract, fetch_rbi, store

SRC = "Banks shall report fraud to the RBI within 14 days of detection.\nAll   branches must comply."


def test_citation_gate():
    assert extract.verify_citation("report fraud to the RBI within 14 days", SRC)
    assert extract.verify_citation("All branches must comply.", SRC)  # whitespace tolerant
    assert not extract.verify_citation("report fraud within 7 days", SRC)  # paraphrase/wrong
    assert extract.verify_citation(["Banks shall report fraud", "must comply"], SRC)  # list form
    assert not extract.verify_citation(["Banks shall report fraud", "invented"], SRC)
    assert not extract.verify_citation("", SRC)


def test_extract_flags_hallucinated_citation(tmp_path):
    db = store.connect(tmp_path / "t.db")
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text) VALUES('RBI','u','t','h',?)", (SRC,))
    fake = json.dumps({"obligations": [
        {"actor": "Banks", "modality": "must", "action": "report fraud", "deadline": "14 days",
         "citation": "Banks shall report fraud to the RBI within 14 days"},
        {"actor": "Banks", "modality": "must", "action": "x", "deadline": None,
         "citation": "Banks shall file within 3 days"}]})
    res = extract.extract_document(db, 1, lambda p: "```json\n" + fake + "\n```", "fake")
    assert res == {"grounded": 1, "ungrounded": 1, "parse_errors": 0}


def test_ledger_detects_tampering(tmp_path):
    db = store.connect(tmp_path / "t.db")
    for i in range(3):
        store.log_event(db, "s", f"in{i}", f"out{i}")
    assert store.verify_chain(db)
    db.execute("UPDATE prov_event SET output_sha='x' WHERE id=2")
    assert not store.verify_chain(db)


def test_parsers():
    html = '<a class="link2" href=" https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12799 ">Master Directions - PSL</a>'
    assert fetch_rbi.parse_listing(html)[0]["id"] == "12799"
    page = '<table class="tablebg"><tr><td>' + "Banks shall comply. " * 40 + '</td></tr></table><table class="tablebg"><tr><td>nav</td></tr></table>'
    assert "Banks shall comply." in fetch_rbi.page_text(page)
    assert "nav" not in fetch_rbi.page_text(page)


def test_bad_json_is_logged_not_fatal(tmp_path):
    db = store.connect(tmp_path / "t.db")
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text) VALUES('RBI','u','t','h',?)", (SRC,))
    res = extract.extract_document(db, 1, lambda p: '{"obligations": [oops', "fake")
    assert res["parse_errors"] == 1 and store.verify_chain(db)
