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
    model = os.environ.get("REG_INTEL_MODEL", "llama-3.3-70b-versatile")
    client = OpenAI(api_key=key, base_url=base)

    def call(prompt: str) -> str:
        r = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content

    return call, model


def extract_document(db, doc_id: int, llm: Callable[[str], str], model: str) -> dict:
    text = db.execute("SELECT raw_text FROM document WHERE id=?", (doc_id,)).fetchone()[0]
    kept = dropped = 0
    for chunk in chunks(text):
        out = llm(PROMPT + chunk)
        store.log_event(db, "extract", chunk, out, model, PROMPT_VER)
        for ob in parse_json(out).get("obligations", []):
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
    return {"grounded": kept, "ungrounded": dropped}
