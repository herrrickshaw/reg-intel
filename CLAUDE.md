# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## Layout and commands
- Package `reg_intel/`: `fetch_rbi.py` (fetch stage, injectable fetcher) -> `extract.py` (v1/v2 extraction,
  grounding gate, lead-in) -> `score.py` (gold recall, precision sheets); `store.py` (SQLite + hash-chained
  ledger); `llm.py` (OpenAI-compatible client behind the `LLM` protocol); `cli.py` (`python -m reg_intel.cli`).
- Verify with `pytest` and `ruff check .` (both offline). Tests must never call a live model or fetch RBI pages:
  pass a fake `llm(prompt) -> str` / `fetch(url) -> html` instead.
- `data/` is generated and gitignored; `gold/` holds labels and URLs only, never RBI text. See README.md.

<!-- source-fetch-toolkit:v1 (managed by ~/bin/add_source_fetch_section.py — edit there, not here) -->
## Source fetching for data collection & literature surveys

When a task needs a primary document (statute, regulator data, government release, paper, filing),
fetch the document itself — never cite a search snippet as if it had been read.

- **On the owner's Mac:** `fetch_source.py "<url | doi:… | celex:… | arxiv:…>" --out ./sources`
  (in `~/bin`; full guide `~/bin/SOURCE_FETCH_TOOLKIT.md`). Scheduled/unattended runs use only
  `bash /Users/umashankar/bin/fetch_source.sh "<target>"` (allowlisted) and then Read the `.txt` it reports.
- **Elsewhere (cloud sessions, CI):** follow the same ladder by hand — direct GET with a browser User-Agent →
  headless browser only if the page needs JavaScript → official alternates (DOI → OpenAlex
  `api.openalex.org/works/doi:<doi>` locations → repository copy; EUR-Lex → EU Publications Office
  `publications.europa.eu/resource/celex/<CELEX>`; arXiv pdf/abs) → Wayback Machine `id_` snapshot.
- **Hard rule:** a CAPTCHA, robot check, Cloudflare/Akamai/WAF challenge or login wall means stop on that
  host. Do not bypass it (no TLS impersonation, stealth browsers, identity rotation or challenge solving);
  look for the same document via another route, or report it as unavailable.
- **Record** for every figure: URL read, title/date, page/table, fetch route; mark abstract-only or
  snippet-only sources. Fetch politely (~1 request/second per host, no bulk crawling).
<!-- /source-fetch-toolkit -->
