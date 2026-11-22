"""reg_intel.cli: every sub-command, with the LLM and HTTP layers faked (no network, cwd in tmp_path)."""
import csv
import json
import sys

import pytest

from reg_intel import cli, extract, fetch_rbi, score, store

URL = "https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=777"
LINES = ["Banks shall report fraud within seven days.", "This heading line is long enough to count."]


def run_cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["reg_intel.cli", *argv])
    cli.main()
    return capsys.readouterr().out


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db_path = str(tmp_path / "d" / "t.db")
    db = store.connect(db_path)
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) VALUES('RBI',?,?,?,?,0)",
               (URL, "Direction Title", "h", "\n".join(LINES)))
    db.commit()
    return tmp_path, db_path, db


def write_gold(tmp_path, cits=None):
    (tmp_path / "gold").mkdir(exist_ok=True)
    cits = LINES[:1] if cits is None else cits
    g = {"url": URL, "title": "Gold Direction Title", "obligations": [{"citation": c} for c in cits]}
    (tmp_path / "gold" / "rbi_777_direction.json").write_text(json.dumps(g))


def test_fetch_delegates_to_run_with_limit_and_db(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(fetch_rbi, "run", lambda limit, db: seen.append((limit, db)))
    run_cli(monkeypatch, capsys, "fetch", "--limit", "3", "--db", "x.db")
    assert seen == [(3, "x.db")]
    run_cli(monkeypatch, capsys, "fetch")
    assert seen[1] == (5, "data/reg_intel.db")               # defaults


def test_verify_intact_and_tampered(env, monkeypatch, capsys):
    _, db_path, db = env
    store.log_event(db, "a", "i", "o")
    assert run_cli(monkeypatch, capsys, "verify", "--db", db_path).strip() == "ledger chain intact"
    db.execute("UPDATE prov_event SET step='forged'")
    db.commit()
    assert run_cli(monkeypatch, capsys, "verify", "--db", db_path).strip() == "LEDGER TAMPERED"


def test_report_counts_grounded_per_document(env, monkeypatch, capsys):
    _, db_path, db = env
    for g in (1, 1, 0):
        db.execute("INSERT INTO obligation(doc_id,citation,grounded) VALUES(1,'c',?)", (g,))
    db.commit()
    out = run_cli(monkeypatch, capsys, "report", "--db", db_path)
    assert out.startswith("Direction Title")
    assert out.rstrip().endswith("grounded 2/3")


def test_gold_reports_problems_and_scores(env, monkeypatch, capsys):
    tmp_path, db_path, db = env
    write_gold(tmp_path, [LINES[0], "invented clause not in text at all"])
    out = run_cli(monkeypatch, capsys, "gold", "--db", db_path)
    assert "gold: 2" in out and "invented clause" in out      # label problem listed, no scoring
    assert "recall" not in out

    write_gold(tmp_path, [LINES[0], LINES[1]])
    db.execute("INSERT INTO obligation(doc_id,citation,grounded) VALUES(1,?,1)", (LINES[0],))
    db.commit()
    out = run_cli(monkeypatch, capsys, "gold", "--db", db_path)
    assert "label problems: none" in out
    assert "'recall': 0.5" in out and "missed" not in out.split("MISSED")[0].split("{")[1]
    assert "MISSED: " + LINES[1][:110] in out


def test_precision_export_then_precision(env, monkeypatch, capsys):
    tmp_path, db_path, db = env
    write_gold(tmp_path)
    db.execute("INSERT INTO obligation(doc_id,citation,modality,grounded,prompt_ver,lead_in) "
               "VALUES(1,?,'must',1,'v2-lines','')", (LINES[0],))
    db.commit()
    out = run_cli(monkeypatch, capsys, "precision-export", "--db", db_path)
    assert out.strip() == f"wrote {score.PRECISION_DIR}/rbi_777_direction.csv"
    sheet = tmp_path / "gold" / "precision" / "rbi_777_direction.csv"
    rows = list(csv.DictReader(open(sheet, newline="")))
    assert [r["citation"] for r in rows] == [LINES[0]]
    rows[0]["human"] = "y"
    with open(sheet, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=score.COLS)
        w.writeheader()
        w.writerows(rows)
    out = run_cli(monkeypatch, capsys, "precision", "--db", db_path)
    assert out.startswith("rbi_777_direction ")
    assert "'strict': 1.0" in out and "'judged': 1" in out


def test_extract_modes_pick_function_and_limit(env, monkeypatch, capsys):
    _, db_path, db = env
    for n in (2, 3):
        db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) VALUES('RBI',?,?,?,?,0)",
                   (f"u{n}", f"T{n}", "h", "x"))
    db.commit()
    monkeypatch.setattr(extract, "openai_compatible_llm", lambda: (lambda p: "", "m"))
    calls = []
    monkeypatch.setattr(extract, "extract_document_v2", lambda d, i, llm, m: calls.append(("v2", i)) or {"v": i})
    monkeypatch.setattr(extract, "extract_document", lambda d, i, llm, m: calls.append(("v1", i)) or {"v": i})
    out = run_cli(monkeypatch, capsys, "extract", "--limit", "2", "--db", db_path)
    assert calls == [("v2", 1), ("v2", 2)]                    # default mode lines, honours --limit
    assert out.splitlines() == ["1 {'v': 1}", "2 {'v': 2}"]
    calls.clear()
    run_cli(monkeypatch, capsys, "extract", "--mode", "generate", "--limit", "1", "--db", db_path)
    assert calls == [("v1", 1)]


def test_extract_skips_documents_that_already_have_obligations(env, monkeypatch, capsys):
    _, db_path, db = env
    db.execute("INSERT INTO obligation(doc_id,citation,grounded) VALUES(1,'c',1)")
    db.commit()
    monkeypatch.setattr(extract, "openai_compatible_llm", lambda: (lambda p: "", "m"))
    monkeypatch.setattr(extract, "extract_document_v2", lambda *a: pytest.fail("should not run"))
    assert run_cli(monkeypatch, capsys, "extract", "--db", db_path) == ""


def test_bad_command_rejected(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, capsys, "nope")


def test_module_entrypoint_runs_main(env, monkeypatch, capsys):
    import runpy
    _, db_path, _ = env
    monkeypatch.setattr(sys, "argv", ["reg_intel.cli", "verify", "--db", db_path])
    monkeypatch.delitem(sys.modules, "reg_intel.cli")
    try:
        runpy.run_module("reg_intel.cli", run_name="__main__")
    finally:
        sys.modules["reg_intel.cli"] = cli
    assert capsys.readouterr().out.strip() == "ledger chain intact"


def test_report_groups_per_document_and_truncates_title(env, monkeypatch, capsys):
    _, db_path, db = env
    long_title = "L" * 100
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) VALUES('RBI','u2',?,'h','x',0)", (long_title,))
    db.execute("INSERT INTO obligation(doc_id,citation,grounded) VALUES(1,'c',1)")
    db.execute("INSERT INTO obligation(doc_id,citation,grounded) VALUES(2,'c',0)")
    db.commit()
    lines = run_cli(monkeypatch, capsys, "report", "--db", db_path).splitlines()
    assert len(lines) == 2                                    # same source, still one line per document
    assert lines[0] == f"{'Direction Title':70} grounded 1/1"
    assert lines[1] == f"{'L' * 70} grounded 0/1"


def test_gold_truncates_title_and_missed_citation(env, monkeypatch, capsys):
    tmp_path, db_path, db = env
    long_cit = "Banks shall " + "w" * 150
    db.execute("UPDATE document SET raw_text=?", (long_cit,))
    db.commit()
    (tmp_path / "gold").mkdir()
    g = {"url": URL, "title": "G" * 100, "obligations": [{"citation": long_cit}]}
    (tmp_path / "gold" / "rbi_777_x.json").write_text(json.dumps(g))
    out = run_cli(monkeypatch, capsys, "gold", "--db", db_path).splitlines()
    assert out[0].startswith("G" * 70 + " | gold: 1") and "G" * 71 not in out[0]
    assert out[-1] == "   MISSED: " + long_cit[:110]
