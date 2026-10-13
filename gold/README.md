# Gold set — insurer / bancassurance topics

Files `gold/*.json`: obligations a careful reader finds in one document, each with a **verbatim** citation.
Check/score with `python -m reg_intel.cli gold` (labels are validated against the stored source text first).

**Status: DRAFT.** First batch labelled by Claude from a full read, *not yet human-reviewed*. A reviewer should
(1) add obligations that were missed, (2) drop non-obligations, (3) flip `status` to `reviewed: <name>, <date>`.
Until then, recall numbers are indicative only. Scorer matches by citation overlap, so it can undercount
when the extractor cites a neighbouring sentence.

| Doc | Gold | Recall | Grounded share | Run |
|---|---|---|---|---|
| RBI Payments Banks – Undertaking of Financial Services (id 13100) | 27 | v1 0.52 → **v2 1.00** | v1 0.72 | gpt-oss-120b |
| RBI Master Direction – Insurance, FEMA (id 10738) | 18 (partial sample) | v1 0.56 → **v2 1.00** | v1 1.00 | gpt-oss-120b |

**v2 (line-indexed) caveats.** The model picks numbered source lines; the citation is the line itself, so the
grounding check is trivially satisfied and no longer measures precision. v2 trades precision for recall:
by hand, ~90% of the lines it selects beyond the gold set are real obligations (many in the partial FEMA
sample were simply unlabelled); the rest is repeal/interpretation boilerplate, list fragments, and one definition.
Stability: recall 1.00 at batch sizes 25 and 40; 0.94 at 60 (one miss); selected-row counts vary ~±10% run to run.
Prompt v2 was written once, not tuned on the gold set; only two documents exist, so this is not a held-out result.

## Precision (review sheets)

`python -m reg_intel.cli precision-export` writes `gold/precision/<doc>.csv`: every distinct line v2 selected.
Fill the `human` column with `y` (operative requirement/prohibition/condition/permission), `f` (real but a
fragment that needs its lead-in line) or `n` (definition, repeal, header, boilerplate). `human` overrides
`claude_draft`; blanks are ignored. `python -m reg_intel.cli precision` reports
**strict** = y/judged and **lenient** = (y+f)/judged. Re-exporting keeps verdicts already entered.

| Doc | Lines | strict | lenient | y / f / n | human-reviewed |
|---|---|---|---|---|---|
| 13100 payments banks | 35 | 0.86 | 0.91 | 30 / 2 / 3 | 0 |
| 10738 FEMA insurance | 55 | 0.91 | 0.98 | 50 / 4 / 1 | 0 |

**Lead-in context (v2.1).** Each selected line now carries `lead_in`: the line(s) that make it readable alone
(`extract.find_lead_in`, computed from the document's own line order, no model call). List items take the nearest
preceding `:` / "following …" line, skipping same-kind siblings, chained up to 2 levels; a lowercase continuation
gets its sentence head. Judged with their lead-in, 8 of the 12 FEMA "fragment" rows became real obligations
(strict 0.76 → 0.91); 13100 was unchanged because its fragments are headers/split-sentence heads, which lead-in
cannot fix. Known gaps: trailing notes such as "(b) Claims may be settled…" attach to the wrong list item
(`v) …that:-`); split-sentence *heads* have no lookahead; heuristics tuned on these two documents only.

All verdicts are Claude's draft (single read, same model family as the extractor) — treat as indicative until a person fills `human`.
Verdicts are matched by line text, so a re-run that selects different lines leaves new lines blank for review.
The 5 mutual-fund lines in 13100 count as real obligations (precision is about obligation-ness, not insurer scope).

Do not commit RBI PDFs/text; labels + URLs only. See `TARGETS.md` for the next documents.
