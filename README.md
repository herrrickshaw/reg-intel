# reg-intel (step 1)

## In plain English

*This section is for readers who do not write software.*

**What is it?** India's central bank, the RBI, publishes long rulebooks called Master Directions. This tool
reads one and produces a list of **obligations**: sentences saying what an organisation must, must not, or
may do. It is an early first step, not a finished product.

**How it works, step by step**
1. **Fetch** the rulebook from the RBI website, pausing between requests so the site is not overloaded.
2. **Ask an AI model** to pick out the obligations.
3. **Check the quotes.** An obligation counts as grounded only if its quoted words appear in the source.
   In the newer line-by-line mode the AI picks numbered lines, so the quote is real by design. That means this
   check can no longer tell you whether the line truly is an obligation.
4. **Keep a tamper-evident diary.** Each AI step is recorded in a chain where altering an old entry can be detected.
5. **Measure quality** against a small "answer key" and review sheets.

**How much should you trust it?** Read this before using any number below.
- The answer key and review verdicts were **drafted by an AI (Claude) and have not been checked by a person**.
- Only two documents have been measured, so the scores may not hold for others.
- Some sentences are cut into pieces or attached to the wrong list item.
- It is **not legal advice**. Always read the original rule.

**Where does your information go?** The text being analysed is sent to an AI service (Groq by default). You can
instead point it at a model running on your own computer.

**Words you will meet**
- **Recall:** of the obligations that should have been found, the share that were found.
- **Precision:** of the lines the tool picked, the share that really are obligations.
- **Gold set:** the answer key used for scoring.

**What you need:** Python, basic comfort with a command line, and either an AI service key or a local model.

## For developers

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
