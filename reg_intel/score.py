"""Score extraction against hand-labelled gold files in gold/*.json.

validate: every gold citation must appear verbatim in the stored source text
          (guards the labels themselves).
score:    recall = share of gold obligations matched by a *grounded* extracted
          citation (containment either way, or SequenceMatcher >= 0.7);
          precision proxy = grounded share of extracted rows for that document.
"""
from __future__ import annotations

import glob
import json
from difflib import SequenceMatcher

from .extract import _norm, verify_citation


def _match(a: str, b: str) -> bool:
    a, b = _norm(a).lower(), _norm(b).lower()
    return bool(a and b) and (a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.7)


def load_gold(pattern: str = "gold/*.json") -> list[dict]:
    return [json.load(open(p)) for p in sorted(glob.glob(pattern))]


def validate(db, g: dict) -> list[str]:
    row = db.execute("SELECT raw_text FROM document WHERE url=?", (g["url"],)).fetchone()
    if not row:
        return [f"document not in store: {g['url']}"]
    return [o["citation"][:80] for o in g["obligations"] if not verify_citation(o["citation"], row[0])]


def score(db, g: dict, prompt_ver: str | None = None) -> dict:
    row = db.execute("SELECT id FROM document WHERE url=?", (g["url"],)).fetchone()
    q, args = "SELECT citation, grounded FROM obligation WHERE doc_id=?", [row[0]]
    if prompt_ver:
        q, args = q + " AND prompt_ver=?", args + [prompt_ver]
    ex = db.execute(q, args).fetchall()
    grounded = [c for c, ok in ex if ok]
    missed = [o["citation"] for o in g["obligations"] if not any(_match(o["citation"], c) for c in grounded)]
    n = len(g["obligations"])
    return {"gold": n, "recall": round((n - len(missed)) / n, 2) if n else None,
            "extracted": len(ex), "grounded_share": round(len(grounded) / len(ex), 2) if ex else None,
            "missed": missed}


# ---- precision: review sheets ----------------------------------------------
# One CSV per gold document in gold/precision/<gold-key>.csv listing every distinct
# line the extractor selected. Verdicts: y = states an operative requirement /
# prohibition / condition / permission; f = fragment (real, but unreadable without its
# lead-in line); n = not an obligation (definition, boilerplate, repeal, header).
# `human` overrides `claude_draft`. Precision is only trustworthy as `human` fills in.
import csv
import os

PRECISION_DIR = "gold/precision"
COLS = ["citation", "modality", "claude_draft", "human", "note"]


def export_review(db, g: dict, key: str, prompt_ver: str) -> str:
    """Write/refresh the review sheet, preserving verdicts already entered (matched by citation)."""
    path = os.path.join(PRECISION_DIR, f"{key}.csv")
    old = {}
    if os.path.exists(path):
        old = {r["citation"]: r for r in csv.DictReader(open(path, newline="", encoding="utf-8"))}
    doc = db.execute("SELECT id FROM document WHERE url=?", (g["url"],)).fetchone()[0]
    seen, rows = set(), []
    for c, m in db.execute("SELECT citation, modality FROM obligation WHERE doc_id=? AND prompt_ver=? ORDER BY id",
                           (doc, prompt_ver)):
        if c in seen:
            continue
        seen.add(c)
        prev = old.get(c, {})
        rows.append({"citation": c, "modality": m, "claude_draft": prev.get("claude_draft", ""),
                     "human": prev.get("human", ""), "note": prev.get("note", "")})
    os.makedirs(PRECISION_DIR, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return path


def precision(key: str) -> dict | None:
    path = os.path.join(PRECISION_DIR, f"{key}.csv")
    if not os.path.exists(path):
        return None
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    verdicts, human_n = [], 0
    for r in rows:
        v = (r["human"] or r["claude_draft"]).strip().lower()
        human_n += bool(r["human"].strip())
        if v in ("y", "f", "n"):
            verdicts.append(v)
    n = len(verdicts)
    if not n:
        return {"rows": len(rows), "judged": 0}
    y, f, no = (verdicts.count(x) for x in "yfn")
    return {"rows": len(rows), "judged": n, "human_reviewed": human_n,
            "strict": round(y / n, 2), "lenient": round((y + f) / n, 2), "y": y, "f": f, "n": no}
