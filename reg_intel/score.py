"""Score extraction against hand-labelled gold files in gold/*.json.

validate: every gold citation must appear verbatim in the stored source text
          (guards the labels themselves).
score:    recall = share of gold obligations matched by a *grounded* extracted
          citation (containment either way, or SequenceMatcher >= 0.7);
          precision proxy = grounded share of extracted rows for that document.
"""
from __future__ import annotations

import csv
import glob
import json
import os
from difflib import SequenceMatcher

from .extract import _norm, verify_citation

GOLD_PATTERN = "gold/*.json"
MATCH_RATIO = 0.7  # SequenceMatcher ratio at/above which two citations count as the same obligation
PRECISION_DIR = "gold/precision"
COLS = ["citation", "lead_in", "modality", "claude_draft", "human", "note"]
VERDICTS = ("y", "f", "n")


def _match(a: str, b: str) -> bool:
    """Containment either way, or fuzzy ratio >= MATCH_RATIO, after whitespace/case normalisation."""
    a, b = _norm(a).lower(), _norm(b).lower()
    return bool(a and b) and (a in b or b in a or SequenceMatcher(None, a, b).ratio() >= MATCH_RATIO)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_gold(pattern: str = GOLD_PATTERN) -> list[dict]:
    """All gold files matching ``pattern``, in sorted path order."""
    return [_read_json(p) for p in sorted(glob.glob(pattern))]


def gold_key(g: dict, pattern: str = GOLD_PATTERN) -> str:
    """Basename (without .json) of the gold file whose name contains this gold entry's RBI id."""
    rbi_id = g["url"].rsplit("id=", 1)[1]
    keys = [os.path.basename(p)[: -len(".json")] for p in glob.glob(pattern)]
    for k in keys:
        if rbi_id in k:
            return k
    raise LookupError(f"no gold file matching id={rbi_id} under {pattern}")


def validate(db, g: dict) -> list[str]:
    """Gold citations that are NOT verbatim in the stored source (empty list = labels are sound)."""
    row = db.execute("SELECT raw_text FROM document WHERE url=?", (g["url"],)).fetchone()
    if not row:
        return [f"document not in store: {g['url']}"]
    return [o["citation"][:80] for o in g["obligations"] if not verify_citation(o["citation"], row[0])]


def score(db, g: dict, prompt_ver: str | None = None) -> dict:
    """Recall of gold obligations against grounded extracted citations (optionally one prompt_ver)."""
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


def _read_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def export_review(db, g: dict, key: str, prompt_ver: str) -> str:
    """Write/refresh the review sheet, preserving verdicts already entered (matched by citation)."""
    path = os.path.join(PRECISION_DIR, f"{key}.csv")
    old = {r["citation"]: r for r in _read_rows(path)} if os.path.exists(path) else {}
    doc = db.execute("SELECT id FROM document WHERE url=?", (g["url"],)).fetchone()[0]
    seen, rows = set(), []
    for c, m, li in db.execute(
            "SELECT citation, modality, lead_in FROM obligation WHERE doc_id=? AND prompt_ver=? ORDER BY id",
            (doc, prompt_ver)):
        if c in seen:
            continue
        seen.add(c)
        prev = old.get(c, {})
        rows.append({"citation": c, "lead_in": li or "", "modality": m, "claude_draft": prev.get("claude_draft", ""),
                     "human": prev.get("human", ""), "note": prev.get("note", "")})
    os.makedirs(PRECISION_DIR, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return path


def precision(key: str) -> dict | None:
    """strict = y/judged, lenient = (y+f)/judged from gold/precision/<key>.csv; None if no sheet."""
    path = os.path.join(PRECISION_DIR, f"{key}.csv")
    if not os.path.exists(path):
        return None
    rows = _read_rows(path)
    verdicts, human_n = [], 0
    for r in rows:
        v = (r["human"] or r["claude_draft"]).strip().lower()
        human_n += bool(r["human"].strip())
        if v in VERDICTS:
            verdicts.append(v)
    n = len(verdicts)
    if not n:
        return {"rows": len(rows), "judged": 0}
    y, f, no = (verdicts.count(x) for x in VERDICTS)
    return {"rows": len(rows), "judged": n, "human_reviewed": human_n,
            "strict": round(y / n, 2), "lenient": round((y + f) / n, 2), "y": y, "f": f, "n": no}
