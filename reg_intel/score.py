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
