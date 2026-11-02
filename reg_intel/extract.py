"""LLM obligation extraction with a verbatim-citation grounding gate.

The gate (verify_citation) is adapted from llm-extract-citation-fix: an
obligation is kept as grounded only if its cited span appears verbatim
(whitespace-tolerant) in the source text. Ungrounded rows are stored with
grounded=0 and must never be shown as findings.
"""
from __future__ import annotations

import json
import logging
import re

from . import store
from .llm import LLM, openai_compatible_llm

__all__ = [
    "PROMPT", "PROMPT_VER", "PROMPT_V2", "PROMPT_V2_VER", "CHUNK_SIZE", "CHUNK_OVERLAP",
    "MIN_LINE_LEN", "LINES_PER_BATCH", "verify_citation", "parse_json", "parse_obligations",
    "chunks", "doc_lines", "numbered_batches", "find_lead_in", "extract_document",
    "extract_document_v2", "backfill_lead_in", "openai_compatible_llm",  # re-exported; lives in llm.py
]

log = logging.getLogger(__name__)

CHUNK_SIZE = 12000  # chars per v1 prompt
CHUNK_OVERLAP = 500  # chars shared by consecutive v1 chunks so a sentence is never split
MIN_LINE_LEN = 25  # shorter source lines (headings, page numbers) are dropped in v2
LINES_PER_BATCH = 40  # numbered lines per v2 prompt (recall 1.00 at 25 and 40, 0.94 at 60)
PARSE_ERROR_STEP = "extract_parse_error"

PROMPT_VER = "v1"
PROMPT = """You extract regulatory obligations from an RBI Master Direction.
Return ONLY JSON: {"obligations":[{"actor":str,"modality":"must|must_not|may",
"action":str,"deadline":str|null,"citation":str}]}.
"citation" MUST be a verbatim quote (one sentence or clause) copied exactly from the text.
Do not paraphrase the citation. If no obligations are present return {"obligations":[]}.

TEXT:
"""


def _norm(s: str) -> str:
    """Collapse all whitespace runs to one space (the tolerance the citation gate allows)."""
    return re.sub(r"\s+", " ", s).strip()


def verify_citation(span: object, source: str) -> bool:
    """True if ``span`` (a string, or a list of strings that must all match) appears verbatim in ``source``."""
    if isinstance(span, list):
        return bool(span) and all(verify_citation(s, source) for s in span)
    if not isinstance(span, str) or not span.strip():
        return False
    return _norm(span) in _norm(source)


def parse_json(text: str) -> dict:
    """First ``{...}`` object in a model reply (tolerates code fences); raises ValueError if none/invalid."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(m.group(0))


def parse_obligations(text: str) -> list[dict]:
    """The ``obligations`` list from a model reply; raises ValueError on malformed JSON."""
    return parse_json(text).get("obligations", [])


def chunks(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    """Yield overlapping character windows of ``text`` (last window may be shorter)."""
    i = 0
    while i < len(text):
        yield text[i:i + size]
        i += size - overlap


def _parse_logged(db, step: str, prompt: str, out: str, model: str, prompt_ver: str) -> list[dict] | None:
    """Log the model call to the ledger and parse it; log a parse-error event and return None if malformed."""
    store.log_event(db, step, prompt, out, model, prompt_ver)
    try:
        return parse_obligations(out)
    except ValueError as e:  # JSONDecodeError subclasses ValueError
        log.warning("%s: unparseable model output (%s)", step, e)
        store.log_event(db, PARSE_ERROR_STEP, prompt, out, model, prompt_ver)
        return None


def _document_text(db, doc_id: int) -> str:
    return db.execute("SELECT raw_text FROM document WHERE id=?", (doc_id,)).fetchone()[0]


def _insert_obligation(db, doc_id: int, ob: dict, citation: str, grounded: bool,
                       model: str, prompt_ver: str, lead_in: str = "") -> None:
    db.execute(
        "INSERT INTO obligation(doc_id,actor,modality,action,deadline,citation,grounded,model,prompt_ver,lead_in)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (doc_id, ob.get("actor"), ob.get("modality"), ob.get("action"), ob.get("deadline"),
         citation, int(grounded), model, prompt_ver, lead_in))


def extract_document(db, doc_id: int, llm: LLM, model: str) -> dict:
    """v1: the model writes quotes; each is kept as grounded only if verbatim in its chunk.

    Returns {"grounded", "ungrounded", "parse_errors"} counts for the document.
    """
    kept = dropped = failed = 0
    for chunk in chunks(_document_text(db, doc_id)):
        obs = _parse_logged(db, "extract", chunk, llm(PROMPT + chunk), model, PROMPT_VER)
        if obs is None:
            failed += 1
            continue
        for ob in obs:
            cit = ob.get("citation")
            grounded = verify_citation(cit, chunk)
            _insert_obligation(db, doc_id, ob, cit if isinstance(cit, str) else json.dumps(cit),
                               grounded, model, PROMPT_VER)
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


def doc_lines(text: str, min_len: int = MIN_LINE_LEN) -> list[str]:
    """Stripped source lines at least ``min_len`` chars long, in document order."""
    return [line.strip() for line in text.split("\n") if len(line.strip()) >= min_len]


def numbered_batches(text: str, per_batch: int = LINES_PER_BATCH, min_len: int = MIN_LINE_LEN):
    """Yield ``doc_lines(text)`` in consecutive slices of ``per_batch``."""
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
    ends_following = cur.endswith(".") and bool(re.search(r"\b(following|undernoted)\b", cur[-80:], re.I))
    return cur.endswith(_COLON) or ends_following


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


def v2_prompt(batch: list[str]) -> str:
    """The v2 prompt for one batch of lines, numbered from 0."""
    return PROMPT_V2 + "\n".join(f"{n}: {line}" for n, line in enumerate(batch))


def extract_document_v2(db, doc_id: int, llm: LLM, model: str,
                        per_batch: int = LINES_PER_BATCH) -> dict:
    """v2: the model selects numbered source lines; the citation is the line itself (grounded=1 by construction).

    Returns {"selected", "parse_errors"} counts. Out-of-range or non-integer line indices are ignored.
    """
    text = _document_text(db, doc_id)
    kept = failed = 0
    all_lines = doc_lines(text)
    for b, batch in enumerate(numbered_batches(text, per_batch)):
        bstart = b * per_batch
        prompt = v2_prompt(batch)
        obs = _parse_logged(db, "extract_v2", prompt, llm(prompt), model, PROMPT_V2_VER)
        if obs is None:
            failed += 1
            continue
        for ob in obs:
            i = ob.get("i")
            if not isinstance(i, int) or not 0 <= i < len(batch):
                continue
            _insert_obligation(db, doc_id, ob, batch[i], True, model, PROMPT_V2_VER,
                               lead_in=find_lead_in(all_lines, bstart + i))
            kept += 1
    db.commit()
    return {"selected": kept, "parse_errors": failed}


def backfill_lead_in(db, prompt_ver: str = PROMPT_V2_VER) -> int:
    """Compute lead_in for existing v2 rows from the stored document text (no model call)."""
    n = 0
    for did, in db.execute("SELECT DISTINCT doc_id FROM obligation WHERE prompt_ver=?", (prompt_ver,)).fetchall():
        lines = doc_lines(_document_text(db, did))
        for oid, cit in db.execute("SELECT id, citation FROM obligation WHERE doc_id=? AND prompt_ver=?",
                                   (did, prompt_ver)).fetchall():
            if cit in lines:
                db.execute("UPDATE obligation SET lead_in=? WHERE id=?",
                           (find_lead_in(lines, lines.index(cit)), oid))
                n += 1
    db.commit()
    return n
