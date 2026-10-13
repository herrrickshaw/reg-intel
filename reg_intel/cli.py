"""python -m reg_intel.cli fetch --limit 5 | extract | report | verify"""
import argparse

from . import extract, fetch_rbi, store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "extract", "report", "verify", "gold"])
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--mode", choices=["lines", "generate"], default="lines",
                    help="lines = v2 line-indexed (default); generate = v1 model-written quotes")
    ap.add_argument("--db", default="data/reg_intel.db")
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch_rbi.run(a.limit, a.db)
        return
    db = store.connect(a.db)
    if a.cmd == "verify":
        print("ledger chain intact" if store.verify_chain(db) else "LEDGER TAMPERED")
    elif a.cmd == "gold":
        from . import score
        for g in score.load_gold():
            bad = score.validate(db, g)
            print(g["title"][:70], "| gold:", len(g["obligations"]), "| label problems:", bad or "none")
            if not bad:
                r = score.score(db, g)
                print("  ", {k: v for k, v in r.items() if k != "missed"})
                for m in r["missed"]:
                    print("   MISSED:", m[:110])
    elif a.cmd == "extract":
        llm, model = extract.openai_compatible_llm()
        for (did,) in db.execute(
                "SELECT id FROM document WHERE id NOT IN (SELECT doc_id FROM obligation) LIMIT ?",
                (a.limit,)).fetchall():
            fn = extract.extract_document_v2 if a.mode == "lines" else extract.extract_document
            print(did, fn(db, did, llm, model))
    else:
        for r in db.execute("SELECT d.title, SUM(o.grounded), COUNT(*) FROM obligation o "
                            "JOIN document d ON d.id=o.doc_id GROUP BY d.id"):
            print(f"{r[0][:70]:70} grounded {r[1]}/{r[2]}")


if __name__ == "__main__":
    main()
