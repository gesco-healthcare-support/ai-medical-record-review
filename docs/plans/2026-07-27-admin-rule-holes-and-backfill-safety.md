---
feature: Close the administrative-rule holes and stop the backfill deleting DOIs on failure
date: 2026-07-27
status: in-progress
base-branch: main
related-issues: []
---

## Goal

A title that names a real document keeps that document's category even when it also names the cover
letter it arrived under, and the DOI backfill never strips a stored date because it could not read
the page.

## Context & decisions

Found by the 7-agent adversarial review of #43-#47 (run on the merged code), then reproduced by hand.
All three are defects in what shipped today.

Reproduced (`match_rules` on main + #47):
- `AME Report with Cover Letter` -> 100, `QME Report - Proof of Service` -> 100,
  `Cover Letter - AME Report of Dr Sample` -> 100, `Email Correspondence - QME Report` -> 100.
  The QME/AME evaluation is the highest-value document in the file and #47 left exactly it
  unprotected, because category 13 was excluded from "document type beats wrapper".
- `Cover Letter - Psychological Evaluation Report` -> 100, `Correspondence - Work Status Report` ->
  100, `Transmittal Letter - Nerve Conduction Study Report` -> 100. These have no keyword rule, so
  before #47 they went to embedding+LLM; now an administrative match answers for them at
  `confidence=high, needs_review=False`, which skips OCR escalation and the review flag.
- This matters because the segmenter is TOLD to fold covers into the document
  (`backend/app/services/gemini.py:34`: "INCLUDING any fax cover sheet, transmittal letter, or
  routing slip that travels with it. A cover page is never its own record") and to take the title
  from the visible header (`gemini.py:43`), so composite "wrapper + document" titles are normal
  output, not an edge case.
- `backend/scripts/backfill_doi.py:80` applies whatever `extract_injury_date` returns, and that
  helper is fail-safe: an expired ADC, a 429 or a moved PDF all return `"-"`, which
  `apply_doi_prefix` turns into "delete the prefix". One run with a stale ADC silently strips every
  DOI in scope and prints a normal-looking "changed N" line. Sarhad's ADC expires about daily and
  the intended run there covers 728 summaries.

Resolved decisions:
- Decision: an administrative match is ignored when the title also names a DOCUMENT
  (report/transcript/note/study/scan/imaging/x-ray/chart/questionnaire/results), because the record
  then contains that document; the wrapper only decides when it is all the title names. "records" is
  deliberately NOT in that set, so "Schedule of Records" and "Cover Letter - Submission of Medical
  Records" stay deterministic at 100.
- Decision: keep `needs_review=False` for a pure administrative match rather than flagging every
  routing slip and proof of service, because the review flag is the reviewer's scarce attention and
  the document-noun rule already removes the destructive cases.
- Decision: `extract_injury_date` grows `strict=False`; only the backfill passes `strict=True`, so
  summarize_row keeps its fail-safe behaviour (a wrong DOI is worse than none at write time) while
  the backfill can tell "this document states none" from "I could not read it".
- Decision: the backfill SKIPS a summary whose extraction failed, reports the count, and exits
  non-zero when every extraction failed, so a stale-ADC run is loud instead of destructive.

## Tasks

### Task 1 - document nouns outrank the wrapper
- what: MODIFY `backend/app/services/classification.py`: add
  `_DOCUMENT_NOUN = re.compile(r"\b(report|transcript|note|notes|study|scan|imaging|x-?ray|chart|questionnaire|results?)\b")`
  and, in `match_rules`, when an administrative pattern matched AND `_DOCUMENT_NOUN` matches, return
  the normal cascade result (`matches[0] if matches else None`) instead of the administrative answer.
- approach: tdd
- acceptance (EARS):
  - WHEN a title names both an administrative wrapper and a document noun, THE SYSTEM SHALL return
    the document's category, or None so the embedding + LLM stages decide.
  - WHEN a title names only administrative paperwork, THE SYSTEM SHALL still return 100.
  - WHEN a title is "AME Report with Cover Letter" or "QME Report - Proof of Service", THE SYSTEM
    SHALL return 13.

### Task 2 - the backfill can tell "none stated" from "unreadable"
- what: MODIFY `backend/app/services/summary_doi.py` `extract_injury_date(..., strict=False)` to
  re-raise instead of returning `"-"` when `strict` is true; MODIFY
  `backend/scripts/backfill_doi.py` `run()` to call it with `strict=True`, skip and count a summary
  whose extraction raised, print `skipped N unreadable`, and `raise SystemExit` without committing
  when every extraction in scope failed.
- approach: tdd
- acceptance (EARS):
  - IF extraction fails for a summary, THEN THE SYSTEM SHALL leave that summary's text unchanged and
    count it as skipped.
  - IF extraction fails for every summary in scope, THEN THE SYSTEM SHALL exit with an error and
    commit nothing.
  - WHEN `--dry-run` is given, THE SYSTEM SHALL leave every text field byte-identical.
  - WHEN run twice with a working extractor, THE SYSTEM SHALL report zero changes the second time.

### Task 3 - tests
- what: EXTEND `backend/tests/test_classification.py` with the reproduced titles (both directions);
  EXTEND `backend/tests/test_backfill_doi.py` with `run()` cases using a monkeypatched extractor:
  dry-run leaves text/verified_text/edited_text identical, a raising extractor strips nothing, an
  all-failed run raises SystemExit, and a working run is idempotent.
- approach: tdd

## Validation loop

1. BE: `uv run ruff check app tests alembic scripts/backfill_doi.py && uv run ruff format --check ...`
2. BE: full suite in the throwaway container (see the memory note); the 4-5 queue-count failures are
   the known live-worker drain.
3. Corpus re-measure: every administrative title in the local DB still resolves to 100, and no title
   that names a document does.

## Risk / rollback

- Blast radius: `match_rules` (every future classification) and the backfill script.
- Rollback: revert; no schema change, no data migration.
