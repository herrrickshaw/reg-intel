"""SQLite store: documents, obligations, and a hash-chained provenance ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS document(
  id INTEGER PRIMARY KEY, source TEXT, url TEXT UNIQUE, title TEXT,
  sha256 TEXT, raw_text TEXT, fetched_at REAL);
CREATE TABLE IF NOT EXISTS obligation(
  id INTEGER PRIMARY KEY, doc_id INTEGER REFERENCES document(id),
  actor TEXT, modality TEXT, action TEXT, deadline TEXT,
  citation TEXT, grounded INTEGER, model TEXT, prompt_ver TEXT);
CREATE TABLE IF NOT EXISTS prov_event(
  id INTEGER PRIMARY KEY, prev_hash TEXT, step TEXT, model TEXT, prompt_ver TEXT,
  input_sha TEXT, output_sha TEXT, ts REAL, hash TEXT);
"""


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def connect(path: str | Path = "data/reg_intel.db") -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def log_event(db, step: str, input_text: str, output_text: str,
              model: str = "", prompt_ver: str = "") -> str:
    """Append a provenance event chained to the previous one's hash."""
    row = db.execute("SELECT hash FROM prov_event ORDER BY id DESC LIMIT 1").fetchone()
    prev = row[0] if row else ""
    ts = time.time()
    body = json.dumps([prev, step, model, prompt_ver, sha(input_text), sha(output_text), ts])
    h = sha(body)
    db.execute("INSERT INTO prov_event(prev_hash,step,model,prompt_ver,input_sha,output_sha,ts,hash)"
               " VALUES(?,?,?,?,?,?,?,?)",
               (prev, step, model, prompt_ver, sha(input_text), sha(output_text), ts, h))
    db.commit()
    return h


def verify_chain(db) -> bool:
    """True if no provenance event has been altered or removed mid-chain."""
    prev = ""
    for _id, p, step, model, pv, i, o, ts, h in db.execute(
            "SELECT id,prev_hash,step,model,prompt_ver,input_sha,output_sha,ts,hash "
            "FROM prov_event ORDER BY id"):
        if p != prev or sha(json.dumps([p, step, model, pv, i, o, ts])) != h:
            return False
        prev = h
    return True
