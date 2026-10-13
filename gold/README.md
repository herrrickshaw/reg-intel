# Gold set — insurer / bancassurance topics

Files `gold/*.json`: obligations a careful reader finds in one document, each with a **verbatim** citation.
Check/score with `python -m reg_intel.cli gold` (labels are validated against the stored source text first).

**Status: DRAFT.** First batch labelled by Claude from a full read, *not yet human-reviewed*. A reviewer should
(1) add obligations that were missed, (2) drop non-obligations, (3) flip `status` to `reviewed: <name>, <date>`.
Until then, recall numbers are indicative only. Scorer matches by citation overlap, so it can undercount
when the extractor cites a neighbouring sentence.

| Doc | Gold | Recall | Grounded share | Run |
|---|---|---|---|---|
| RBI Payments Banks – Undertaking of Financial Services (id 13100) | 27 | 0.52 | 0.72 | gpt-oss-120b, prompt v1 |
| RBI Master Direction – Insurance, FEMA (id 10738) | 18 | 0.56 | 1.00 | gpt-oss-120b, prompt v1 |

Do not commit RBI PDFs/text; labels + URLs only. See `TARGETS.md` for the next documents.
