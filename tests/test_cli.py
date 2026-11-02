"""CLI smoke tests on a temporary store (no network, no model)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from reg_intel import cli, store


def test_help_lists_every_command():
    out = subprocess.run([sys.executable, "-m", "reg_intel.cli", "--help"], capture_output=True, text=True, check=True)
    for c in ["fetch", "extract", "report", "verify", "gold", "precision-export", "precision"]:
        assert c in out.stdout


def test_verify_and_report_on_fresh_store(tmp_path, capsys):
    db_path = str(tmp_path / "t.db")
    cli.main(["verify", "--db", db_path])
    assert capsys.readouterr().out.strip() == "ledger chain intact"
    db = store.connect(db_path)
    db.execute("INSERT INTO document(source,url,title,sha256,raw_text) VALUES('RBI','u','Title','h','x')")
    db.executemany("INSERT INTO obligation(doc_id,citation,grounded) VALUES(1,?,?)", [("a", 1), ("b", 0)])
    db.commit()
    cli.main(["report", "--db", db_path])
    assert capsys.readouterr().out.rstrip().endswith("grounded 1/2")
    store.log_event(db, "s", "in", "out")
    db.execute("UPDATE prov_event SET output_sha='tampered'")
    db.commit()
    cli.main(["verify", "--db", db_path])
    assert capsys.readouterr().out.strip() == "LEDGER TAMPERED"


def test_extract_without_key_fails_before_touching_network(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("REG_INTEL_API_KEY", raising=False)
    with pytest.raises(KeyError):
        cli.main(["extract", "--db", str(tmp_path / "t.db")])
