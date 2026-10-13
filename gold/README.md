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

Do not commit RBI PDFs/text; labels + URLs only. See `TARGETS.md` for the next documents.
