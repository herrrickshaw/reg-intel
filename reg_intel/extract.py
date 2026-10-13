"""LLM obligation extraction with a verbatim-citation grounding gate.

The gate (_verify_citation) is adapted from llm-extract-citation-fix: an
obligation is kept as grounded only if its cited span appears verbatim
(whitespace-tolerant) in the source text. Ungrounded rows are stored with
grounded=0 and must never be shown as findings.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable

from . import store

PROMPT_VER = "v1"
PROMPT = """You extract regulatory obligations from an RBI Master Direction.
Return ONLY JSON: {"obligations":[{"actor":str,"modality":"must|must_not|may",
"action":str,"deadline":str|null,"citation":str}]}.
"citation" MUST be a verbatim quote (one sentence or clause) copied exactly from the text.
Do not paraphrase the citation. If no obligations are present return {"obligations":[]}.

TEXT:
"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def verify_citation(span: object, source: str) -> bool:
    if isinstance(span, list):
        return bool(span) and all(verify_citation(s, source) for s in span)
    if not isinstance(span, str) or not span.strip():
        return False
    return _norm(span) in _norm(source)


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(m.group(0))


def chunks(text: str, size: int = 12000, overlap: int = 500):
    i = 0
    while i < len(text):
        yield text[i:i + size]
        i += size - overlap


def openai_compatible_llm() -> tuple[Callable[[str], str], str]:
    """Groq by default (GROQ_API_KEY); override with REG_INTEL_BASE_URL/KEY/MODEL."""
    from openai import OpenAI
    base = os.environ.get("REG_INTEL_BASE_URL", "https://api.groq.com/openai/v1")
    key = os.environ.get("REG_INTEL_API_KEY") or os.environ["GROQ_API_KEY"]
    model = os.environ.get("REG_INTEL_MODEL", "openai/gpt-oss-120b")
    client = OpenAI(api_key=key, base_url=base)

    def call(prompt: str) -> str:
        import time
        for attempt in range(4):  # free-tier TPM limits: back off and retry
            try:
                r = client.chat.completions.create(
                    model=model, temperature=0, max_tokens=5000,
                    response_format={"type": "json_object"},
                    messages=[{"role": "user", "content": prompt}])
                return r.choices[0].message.content
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status_code", None) == 400 and "json_validate_failed" in str(e):
                    return ""  # reasoning model ran out of tokens; caller logs a parse error
                if attempt == 3 or getattr(e, "status_code", None) not in (413, 429):
                    raise
                time.sleep(20 * (attempt + 1))

    return call, model


def extract_document(db, doc_id: int, llm: Callable[[str], str], model: str) -> dict:
    text = db.execute("SELECT raw_text FROM document WHERE id=?", (doc_id,)).fetchone()[0]
    kept = dropped = failed = 0
    for chunk in chunks(text):
        out = llm(PROMPT + chunk)
        store.log_event(db, "extract", chunk, out, model, PROMPT_VER)
        try:
            obs = parse_json(out).get("obligations", [])
        except ValueError:  # JSONDecodeError subclasses ValueError
            store.log_event(db, "extract_parse_error", chunk, out, model, PROMPT_VER)
            failed += 1
            continue
        for ob in obs:
            grounded = verify_citation(ob.get("citation"), chunk)
            db.execute(
                "INSERT INTO obligation(doc_id,actor,modality,action,deadline,citation,grounded,model,prompt_ver)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (doc_id, ob.get("actor"), ob.get("modality"), ob.get("action"), ob.get("deadline"),
                 ob.get("citation") if isinstance(ob.get("citation"), str) else json.dumps(ob.get("citation")),
                 int(grounded), model, PROMPT_VER))
            kept += grounded
            dropped += not grounded
    db.commit()
    return {"grounded": kept, "ungrounded": dropped, "parse_errors": failed}


# ---- v2: line-indexed extraction -------------------------------------------
# The model classifies numbered source lines instead of generating quotes, so the
# citation is the source line itself (verbatim by construction). The grounding gate
# therefore stops being informative in this mode; precision must be judged by
# reviewing flagged lines, and recall by the gold set.
PROMPT_V2_VER = "v2-lines"
PROMPT_V2 = """You review numbered lines from an RBI regulatory document.
Select EVERY line that states a requirement, prohibition, condition, permission or
deadline that applies to a regulated entity, its Board, staff or a customer-facing
process. Include sub-clauses and list items. Exclude: definitions, headings, table of
contents, letterhead, addresses, signatures, footnotes, repeal/saving text, and
pure cross-references. When unsure, INCLUDE the line.
Return ONLY JSON: {"obligations":[{"i":<line number>,"actor":str,"modality":"must|must_not|may",
"action":str,"deadline":str|null}]}. Do not quote text; refer to lines by number only.

LINES:
"""


def doc_lines(text: str, min_len: int = 25) -> list[str]:
    return [l.strip() for l in text.split("\n") if len(l.strip()) >= min_len]


def numbered_batches(text: str, per_batch: int = 40, min_len: int = 25):
    lines = doc_lines(text, min_len)
    for k in range(0, len(lines), per_batch):
        yield lines[k:k + per_batch]


_MARKER = re.compile(r"^\(?([ivxlc]+|[a-z]|\d+)\)\s", re.I)
_PARA = re.compile(r"^\d+\.\s")
_COLON = (":", ":-", ":—", ":–")
_LISTY_END = (";", " and", ",", " and,")


def _marker_kind(line: str) -> str | None:
    """'digit' for (4)/4), 'roman' for i)/(iii)/vi), 'alpha' for (a)/b); None if unmarked."""
    m = _MARKER.match(line)
    if not m:
        return None
    t = m.group(1).lower()
    if t.isdigit():
        return "digit"
    return "roman" if re.fullmatch(r"[ivxlc]+", t) and (len(t) > 1 or t in "iv") else "alpha"


def _is_leadin(line: str) -> bool:
    cur = line.rstrip()
    return cur.endswith(_COLON) or (cur.endswith(".") and bool(re.search(r"\b(following|undernoted)\b", cur[-80:], re.I)))


def _is_item(line: str) -> bool:
    return bool(_MARKER.match(line)) or line.rstrip().endswith(_LISTY_END)


def find_lead_in(lines: list[str], i: int, depth: int = 2, window: int = 15) -> str:
    """Context that makes line i readable on its own, as a single string ('' if none needed/found).

    - A list item (marker like 'ii)' / '(b)', or ending ';' / 'and' / ',') takes the nearest preceding
      line ending with ':', skipping siblings (same marker kind). A lead-in that is itself an item gets
      its own lead-in (depth <= 2).
    - An unmarked line right after a list-ish line (ending ';' / 'and' / ',') belongs to that list.
    - Otherwise a line starting lowercase continues a sentence: prepend the sentence head.
    Never crosses the start of an earlier numbered paragraph ('12. ...')."""
    line = lines[i]
    if _PARA.match(line):  # starts a new numbered paragraph: self-contained or a head, never a list tail
        return ""
    kind = _marker_kind(line)
    if kind is None and line[:1].islower():  # unmarked lowercase start = sentence continuation
        j = i - 1
        while j > 0 and lines[j][:1].islower() and not _MARKER.match(lines[j]):
            j -= 1
        return " ".join(lines[max(j, 0):i]) if j >= 0 else ""
    tail_of_list = i > 0 and kind is None and lines[i - 1].rstrip().endswith(_LISTY_END) and depth > 0
    if kind or _is_item(line) or tail_of_list:
        for j in range(i - 1, max(i - window, -1) - 1, -1):
            cur = lines[j].rstrip()
            if _is_leadin(cur) and not (kind and _marker_kind(cur) == kind):
                up = find_lead_in(lines, j, depth - 1, window) if depth > 0 and _is_item(cur) else ""
                return (up + " " + cur).strip()
            if _PARA.match(cur):
                return ""
        return ""
    return ""


def extract_document_v2(db, doc_id: int, llm: Callable[[str], str], model: str,
                        per_batch: int = 40) -> dict:
    text = db.execute("SELECT raw_text FROM document WHERE id=?", (doc_id,)).fetchone()[0]
    kept = failed = 0
    all_lines = doc_lines(text)
    for bstart, batch in zip(range(0, len(all_lines), per_batch), numbered_batches(text, per_batch)):
        prompt = PROMPT_V2 + "\n".join(f"{n}: {l}" for n, l in enumerate(batch))
        out = llm(prompt)
        store.log_event(db, "extract_v2", prompt, out, model, PROMPT_V2_VER)
        try:
            obs = parse_json(out).get("obligations", [])
        except ValueError:
            store.log_event(db, "extract_parse_error", prompt, out, model, PROMPT_V2_VER)
            failed += 1
            continue
        for ob in obs:
            i = ob.get("i")
            if not isinstance(i, int) or not 0 <= i < len(batch):
                continue
            db.execute(
                "INSERT INTO obligation(doc_id,actor,modality,action,deadline,citation,grounded,model,prompt_ver,lead_in)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (doc_id, ob.get("actor"), ob.get("modality"), ob.get("action"), ob.get("deadline"),
                 batch[i], 1, model, PROMPT_V2_VER, find_lead_in(all_lines, bstart + i)))
            kept += 1
    db.commit()
    return {"selected": kept, "parse_errors": failed}


def backfill_lead_in(db, prompt_ver: str = PROMPT_V2_VER) -> int:
    """Compute lead_in for existing v2 rows from the stored document text (no model call)."""
    n = 0
    for did, in db.execute("SELECT DISTINCT doc_id FROM obligation WHERE prompt_ver=?", (prompt_ver,)).fetchall():
        lines = doc_lines(db.execute("SELECT raw_text FROM document WHERE id=?", (did,)).fetchone()[0])
        for oid, cit in db.execute("SELECT id, citation FROM obligation WHERE doc_id=? AND prompt_ver=?",
                                   (did, prompt_ver)).fetchall():
            if cit in lines:
                db.execute("UPDATE obligation SET lead_in=? WHERE id=?", (find_lead_in(lines, lines.index(cit)), oid))
                n += 1
    db.commit()
    return n
