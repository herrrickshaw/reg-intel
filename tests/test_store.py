"""reg_intel.store: schema migration and hash-chained ledger."""
import sqlite3

from reg_intel import store


def test_sha_known_value():
    assert store.sha("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_empty_chain_is_valid(tmp_path):
    assert store.verify_chain(store.connect(tmp_path / "t.db"))


def test_chain_links_and_detects_mid_chain_deletion(tmp_path):
    db = store.connect(tmp_path / "t.db")
    hs = [store.log_event(db, "s", f"in{i}", f"out{i}", "m", "v") for i in range(4)]
    prevs = [r[0] for r in db.execute("SELECT prev_hash FROM prov_event ORDER BY id")]
    assert prevs == [""] + hs[:-1]
    db.execute("DELETE FROM prov_event WHERE id=2")
    assert not store.verify_chain(db)


def test_chain_detects_reordering_of_step_name(tmp_path):
    db = store.connect(tmp_path / "t.db")
    store.log_event(db, "fetch", "a", "b")
    db.execute("UPDATE prov_event SET step='extract'")
    assert not store.verify_chain(db)


def test_migrates_old_obligation_table(tmp_path):
    p = tmp_path / "old.db"
    old = sqlite3.connect(p)
    old.execute("CREATE TABLE obligation(id INTEGER PRIMARY KEY, doc_id INTEGER, actor TEXT, modality TEXT, action TEXT, "
                "deadline TEXT, citation TEXT, grounded INTEGER, model TEXT, prompt_ver TEXT)")
    old.commit(); old.close()
    db = store.connect(p)
    assert "lead_in" in [r[1] for r in db.execute("PRAGMA table_info(obligation)")]
    store.connect(p)                                   # idempotent
