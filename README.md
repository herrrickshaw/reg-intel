# reg-intel (step 1)

Fetch RBI Master Directions → extract obligations with an LLM → keep only those whose
citation appears **verbatim** in the source → log every step in a hash-chained ledger.

```bash
pip install -e . pytest
pytest                                   # offline, no network/LLM
python -m reg_intel.cli fetch --limit 5  # polite 2s delay between requests
GROQ_API_KEY=... python -m reg_intel.cli extract --limit 5
python -m reg_intel.cli report           # grounded/total per document
python -m reg_intel.cli verify           # ledger tamper check
```
Any OpenAI-compatible endpoint works (e.g. local LM Studio): set `REG_INTEL_BASE_URL`,
`REG_INTEL_API_KEY`, `REG_INTEL_MODEL`. Output is not legal advice; always read the cited source.

Next: gold set + recall metric (`gold/`), xref graph, change feed. Citation gate adapted from
llm-extract-citation-fix; fetch pattern from gazette-translated-indexed.
