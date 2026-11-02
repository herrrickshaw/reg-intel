"""CLI: python -m reg_intel.cli fetch --limit 5 | extract [--mode lines|generate] | report | verify
| gold | precision-export | precision"""
from __future__ import annotations

import argparse
import sqlite3

from . import extract, fetch_rbi, score, store


def cmd_verify(db: sqlite3.Connection, a: argparse.Namespace) -> None:
    print("ledger chain intact" if store.verify_chain(db) else "LEDGER TAMPERED")


def cmd_precision_export(db: sqlite3.Connection, a: argparse.Namespace) -> None:
    for g in score.load_gold():
        print("wrote", score.export_review(db, g, score.gold_key(g), extract.PROMPT_V2_VER))


def cmd_precision(db: sqlite3.Connection, a: argparse.Namespace) -> None:
    for g in score.load_gold():
        key = score.gold_key(g)
        print(key, score.precision(key))


def cmd_gold(db: sqlite3.Connection, a: argparse.Namespace) -> None:
    for g in score.load_gold():
        bad = score.validate(db, g)
        print(g["title"][:70], "| gold:", len(g["obligations"]), "| label problems:", bad or "none")
        if not bad:
            r = score.score(db, g)
            print("  ", {k: v for k, v in r.items() if k != "missed"})
            for m in r["missed"]:
                print("   MISSED:", m[:110])


def cmd_extract(db: sqlite3.Connection, a: argparse.Namespace) -> None:
    llm, model = extract.openai_compatible_llm()
    fn = extract.extract_document_v2 if a.mode == "lines" else extract.extract_document
    for (did,) in db.execute(
            "SELECT id FROM document WHERE id NOT IN (SELECT doc_id FROM obligation) LIMIT ?",
            (a.limit,)).fetchall():
        print(did, fn(db, did, llm, model))


def cmd_report(db: sqlite3.Connection, a: argparse.Namespace) -> None:
    for r in db.execute("SELECT d.title, SUM(o.grounded), COUNT(*) FROM obligation o "
                        "JOIN document d ON d.id=o.doc_id GROUP BY d.id"):
        print(f"{r[0][:70]:70} grounded {r[1]}/{r[2]}")


COMMANDS = {  # name -> handler(db, args); "fetch" opens its own store
    "verify": cmd_verify,
    "precision-export": cmd_precision_export,
    "precision": cmd_precision,
    "gold": cmd_gold,
    "extract": cmd_extract,
    "report": cmd_report,
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m reg_intel.cli")
    ap.add_argument("cmd", choices=["fetch", *COMMANDS])
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--mode", choices=["lines", "generate"], default="lines",
                    help="lines = v2 line-indexed (default); generate = v1 model-written quotes")
    ap.add_argument("--db", default=store.DEFAULT_DB_PATH)
    return ap


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    if a.cmd == "fetch":
        fetch_rbi.run(a.limit, a.db)
        return
    COMMANDS[a.cmd](store.connect(a.db), a)


if __name__ == "__main__":
    main()
