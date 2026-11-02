# reg-intel — auditable obligation extraction from RBI Master Directions

Fetches RBI Master Directions, asks an LLM to extract the regulatory obligations in them
(actor, modality, action, deadline), keeps only obligations whose citation appears
**verbatim** in the source text, and records every fetch and model call in a hash-chained
provenance ledger. Built for compliance research on Indian banks and insurers; output is a
local SQLite store plus CLI reports and CSV review sheets. Step 1 of a larger programme.

> Status: active, research-only. Output is not legal advice; always read the cited source.
> RBI pages are fetched politely (2 s delay) and never committed; `gold/` holds labels and
> URLs only.

## What it does
- Scrapes the RBI Master Directions listing and the inline text of each direction page (`fetch_rbi.py`).
- Extracts obligations with any OpenAI-compatible chat model (Groq by default), in two modes:
  v1 "generate" (model writes quotes) and v2 "lines" (model selects numbered source lines, the default).
- Grounding gate: a v1 citation is stored as `grounded=1` only if it is a whitespace-tolerant verbatim
  span of the source; ungrounded rows are kept with `grounded=0` and never reported as findings.
- Attaches lead-in context to v2 list items and sentence fragments (`extract.find_lead_in`) with no model call.
- Hash-chained ledger of every step (`fetch`, `extract`, `extract_v2`, `extract_parse_error`); `verify` detects
  any altered or removed event.
- Scores recall against hand-labelled gold files and precision from human-reviewable CSV sheets (`score.py`).

## Quick start
```bash
git clone https://github.com/herrrickshaw/reg-intel
cd reg-intel
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # offline: no network, no LLM
python -m reg_intel.cli fetch --limit 5  # polite 2 s delay between requests
GROQ_API_KEY=... python -m reg_intel.cli extract --limit 5
python -m reg_intel.cli report           # grounded/total per document
python -m reg_intel.cli verify           # ledger tamper check
```

Other commands: `extract --mode generate` (v1), `gold` (recall vs `gold/*.json`),
`precision-export` / `precision` (review sheets), `--db PATH` on any command.

## How it works
| Stage | Module | Output |
|---|---|---|
| fetch listing → detail page → inline text | `reg_intel/fetch_rbi.py` (`parse_listing`, `page_text`, `fetch_one`) | `document` rows + `fetch` ledger event |
| extract (v1 quotes or v2 line indices) | `reg_intel/extract.py` (`extract_document`, `extract_document_v2`) | `obligation` rows + `extract*` events |
| ground | `extract.verify_citation` (v1); v2 citations are source lines by construction | `grounded` flag |
| lead-in context | `extract.find_lead_in`, `backfill_lead_in` | `obligation.lead_in` |
| score | `reg_intel/score.py` (`validate`, `score`, `export_review`, `precision`) | CLI output, `gold/precision/*.csv` |
| ledger | `reg_intel/store.py` (`log_event`, `verify_chain`) | `prov_event` rows |
| model client | `reg_intel/llm.py` (`LLM` protocol, `make_llm`, `openai_compatible_llm`) | — |

Design decisions:
- **Verbatim citation gate** (adapted from `llm-extract-citation-fix`): an obligation the model cannot
  anchor to the source text is never a finding.
- **v2 line-indexed extraction** trades the gate for recall: the model picks line numbers, so citations are
  verbatim by construction and precision must instead be judged by review sheets (see `gold/README.md`).
- **LLM behind a Protocol**: extraction depends on a `prompt -> str` callable, so tests drive it with fakes;
  only `llm.openai_compatible_llm` touches the network, with backoff on 413/429 for free-tier limits.
- **No PDFs**: RBI's PDF host sits behind an anti-bot challenge, so only directions with inline HTML text
  are ingested (pages under 500 characters of text are skipped). Fetch pattern from `gazette-translated-indexed`.
- **SQLite + hash chain** rather than plain files so the audit trail travels with the data.

## Repository layout
| Path | Purpose |
|---|---|
| `reg_intel/cli.py` | `python -m reg_intel.cli <cmd>` entry point, one handler per command |
| `reg_intel/fetch_rbi.py` | RBI listing/detail parsers and the fetch stage (injectable fetcher) |
| `reg_intel/extract.py` | prompts, grounding gate, v1/v2 extractors, lead-in heuristics |
| `reg_intel/llm.py` | OpenAI-compatible client, retry policy, env settings |
| `reg_intel/score.py` | gold validation, recall, precision review sheets |
| `reg_intel/store.py` | SQLite schema, migration, provenance ledger |
| `tests/` | offline pytest suite (fixture text, fake LLM/fetcher) |
| `gold/*.json` | draft gold labels (verbatim citations + URL, no source text); `gold/README.md` has the metrics |
| `gold/precision/*.csv` | review sheets; fill the `human` column |
| `gold/TARGETS.md` | next documents to label |
| `data/` | generated, gitignored: `reg_intel.db` |

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| RBI Master Directions listing, https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx | links + titles | scraped, 2 s delay, research User-Agent | public regulator pages; PDF host blocks scripts, so PDF-only directions are skipped |
| RBI direction detail pages, `BS_ViewMasDirections.aspx?id=<id>` | inline text of the largest `table.tablebg` | scraped | text is stored locally only, never committed |

## Configuration
| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | API key when no `REG_INTEL_API_KEY` is set (required for `extract`) |
| `REG_INTEL_API_KEY` | — | key for any OpenAI-compatible endpoint (e.g. local LM Studio) |
| `REG_INTEL_BASE_URL` | `https://api.groq.com/openai/v1` | endpoint |
| `REG_INTEL_MODEL` | `openai/gpt-oss-120b` | model name |
| `--db` | `data/reg_intel.db` | SQLite store path |

Keys are read from the environment only and are never committed (`.env*` is gitignored).

## Testing
```bash
pytest -q          # 31 offline tests
ruff check .
```
CI (`.github/workflows/ci.yml`) runs both on push and pull request. Tests never call a model or fetch RBI pages.

## Limitations and known issues
- Gold set is a **draft** labelled by Claude, not yet human-reviewed; recall figures (v2 1.00 on two
  documents) are indicative only and not held-out. Precision review verdicts are also drafts until the
  `human` column is filled (`gold/README.md`).
- v2 makes the grounding gate trivially true; it does not measure precision in that mode.
- Lead-in heuristics were tuned on two documents; trailing notes can attach to the wrong list item and
  split-sentence heads get no lookahead.
- PDF-only directions (including the 2025 consolidated ones listed in `gold/TARGETS.md`) are not ingested.
- Selected-row counts vary roughly ±10 % run to run at temperature 0.
- Planned, not built: cross-reference graph, change feed, IRDAI adapter.

## Related repositories
- `llm-extract-citation-fix` — origin of the verbatim citation gate.
- `gazette-translated-indexed` — origin of the polite fetch pattern.

## Licence
No licence file yet — all rights reserved.
