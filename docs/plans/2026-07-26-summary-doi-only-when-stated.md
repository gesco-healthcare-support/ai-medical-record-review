---
feature: Summary DOI only when the sub-document itself states one
date: 2026-07-26
status: in-progress
base-branch: main
related-issues: []
---

## Goal

A summary shows a `**DOI**:` prefix only when the summarized sub-document itself states an
injury date (using exactly the stated date), by extracting the DOI per-document in ISOLATION
(vision) at summarize time; existing summaries are backfilled the same way.

## Context & decisions

Why now: every summary carries a DOI - the claim's DOI - even on documents that never state one.

Root cause (PROVEN on real data, local :8080): the Gemini SEGMENTATION model propagates the
claim DOI onto documents that do not state it, because it reads a whole window (a claim form +
its neighbours) at once. On doc `ef5f6bd4` every MRI/X-ray row got `09/25/2023` although the
imaging pages show only "Date of exam: 28-Mar-2024" and carry no per-page DOI stamp (ruled out
by OCR).

Resolved Open Decisions (each with the evidence that settled it):
- REJECTED - deterministic OCR output guard: false-drops genuine DOIs because Tesseract garbles
  the digits it matches (`05/08/2022` OCR'd as `05/68/2022` / `O5/88%2022`).
- REJECTED - segmentation prompt hardening: ineffective. A hardened anti-inference `"i"` prompt
  (PROMPT_VERSION "3") still stamped `09/25/2023` on all 41 rows incl. every MRI. Reverted.
- CHOSEN - per-sub-document ISOLATED vision extraction at summarize time: send ONLY that
  document's pages and ask "does THIS document state a DOI?". It cannot propagate (no neighbours)
  and reads dates from the image (not lossy OCR). VALIDATED: MRI p28-29/p30 -> `-`; QME p18-19 +
  Work-Status p22-27 -> `09/25/2023` (all 4 correct).
- CHOSEN - backfill existing summaries via the same isolated extraction (now reliable), rewriting
  their stored DOI prefix.
- Kill-switch: gate the extra call behind a setting `summary_doi_extract` (mirrors
  `summary_verify`), default on.

## All needed context

- `summarize_row` (`summarize_engine.py:65`) builds `doi_final` from `row["injury_date"]`
  (:96) and prefixes it to `summaryText` (:116) and `verifiedText` (:109). `text` (OCR) is
  extracted at :85; two Gemini calls run via `_generate`. The fix REPLACES the DOI source only.
- callers of `summarize_row`: `tasks.py:420` (main summarize run, on a ThreadPool),
  `documents.py:631` (resummarize), `bundles.py:51` (export, passes `verify=False`). All inherit
  the new default; bundle keeps its fast path but still gets correct DOIs.
- Isolated vision call pattern to mirror: `segment_engine._window_rows` (`segment_engine.py:52-67`)
  - `PdfReader`/`PdfWriter` build a sub-PDF, `types.Part.from_bytes(..., "application/pdf")`,
  `generate_with_retry(client, model=..., contents=[part, PROMPT], config=...)`.
- `settings.genai_model` is the vision/segmentation model (gemini-2.5-flash); use it for the DOI
  call. `settings.summary_verify` in `config.py` is the pattern for the new flag.
- `Summary` has `source_text`, `row_start`, `row_end`, and `document` (backfill inputs);
  `Document.stored_path` is the PDF. Multi-DOI is possible (comma-joined).
- FE: NO change - `summaries-view.tsx:24` parses whatever `**DOI**:` prefix the backend emits.
- Cost bound: a sub-document's DOI, when stated, is on its first pages, so the isolated call
  sends at most the first 5 pages of the sub-document (bounds payload on long QME/depositions).

## Tasks (implementation blueprint)

### Task 1 - kill-switch setting
- what: MODIFY `backend/app/config.py` - add `summary_doi_extract: bool = True` (env
  `SUMMARY_DOI_EXTRACT`) next to `summary_verify`.
- pattern: `summary_verify` in `config.py`.
- approach: code
- acceptance (EARS): The system shall read `summary_doi_extract` from settings (env-overridable).

### Task 2 - isolated DOI extraction service
- what: CREATE `backend/app/services/summary_doi.py` with
  `extract_injury_date(pdf_path, start, end, model=None) -> str`. Build a sub-PDF of pages
  `[start .. min(end, start+4)]`, send it as a vision `Part` with the isolation prompt (temp 0,
  small max_output_tokens), then extract MM/DD/YYYY tokens from the reply with a regex (join
  multiple with `", "`). Return `"-"` when no date is found, the input is blank, or any error
  occurs (fail-safe; log a warning) - a wrong/propagated DOI is worse than none.
- pattern: `segment_engine._window_rows` (vision Part) + `summary_verify.verify_summary`
  (fail-safe structure).
- approach: tdd
- acceptance (EARS):
  - WHEN the isolated reply contains a date, THE SYSTEM SHALL return it as MM/DD/YYYY (multiple
    dates joined `", "`).
  - WHEN the reply contains no date, is blank, or the call raises, THE SYSTEM SHALL return `"-"`.

### Task 3 - use isolated extraction in summarize_row
- what: MODIFY `backend/app/services/summarize_engine.py` - add param `extract_doi=None`
  (defaults to `settings.summary_doi_extract`); when on, `injury = extract_injury_date(pdf_path,
  row["start"], row["end"], model)`, else `injury = row["injury_date"]`; build `doi_final` from
  `injury`. `verifiedText`/`summaryText` use the same `doi_final`.
- pattern: existing `doi_final` at summarize_engine.py:96.
- approach: tdd
- acceptance (EARS):
  - WHEN `extract_doi` and the isolated extraction returns a date, THE SYSTEM SHALL prefix
    `**DOI**:<date>,`.
  - WHEN the isolated extraction returns `"-"`, THE SYSTEM SHALL omit the `**DOI**` prefix.
  - WHEN `extract_doi` is False, THE SYSTEM SHALL fall back to `row["injury_date"]` (legacy).

### Task 4 - backfill existing summaries
- what: CREATE `backend/scripts/backfill_doi.py` (idempotent, `--dry-run`). For each `Summary`,
  re-extract the DOI in isolation from `(document.stored_path, row_start, row_end)` and rewrite
  the leading `**DOI**:...,` prefix on `text`, `verified_text`, `edited_text` via a pure
  `apply_doi_prefix(body, injury)` helper (in `summary_doi.py`, unit-tested), removing it when
  the extraction is `"-"`. Print a changed-count. Run per box after deploy.
- pattern: `backend/scripts/migrate_from_sqlite.py` (bootstrap + argparse).
- approach: test-after (unit-test `apply_doi_prefix`; the DB walk is a script)
- acceptance (EARS):
  - WHEN run, THE SYSTEM SHALL set each summary's DOI prefix to the isolated-extraction result
    (or remove it), changing no other content.
  - WHEN run again, THE SYSTEM SHALL make zero changes (idempotent).

### Task 5 - tests + live verify
- what: `backend/tests/test_summary_doi.py` (Task 2 parser + fail-safe with the model mocked;
  `apply_doi_prefix` cases). EXTEND `backend/tests/test_summarize_engine.py` (Task 3: extract_doi
  on -> prefix from extraction; `"-"` -> no prefix; extract_doi off -> legacy). Live: summarize a
  known-propagated imaging row (DOI now omitted) and a medical-legal row (DOI kept).
- approach: tdd / test-after
- acceptance (EARS): The system shall pass the full backend suite with new-code coverage >= 80%.

## Validation loop

Run from the api container (source synced via `docker cp`):
1. `uv run ruff check . && uv run ruff format --check .` (whole repo)
2. `uv run pytest tests/test_summary_doi.py tests/test_summarize_engine.py -q`
3. `uv run pytest -q` (full; the ~5 enqueue/queue-count failures are the known live-RQ-worker
   drain on the local stack, not this diff - green in CI)
4. Live (Vertex, ADC at `/secrets/adc.json`): summarize `ef5f6bd4` p28-29 (imaging -> no DOI) and
   p22-27 (Work Status -> DOI kept).

## Risk / rollback

- Blast radius: the summary DOI prefix + one extra Gemini vision call per summarized row (gated
  by `summary_doi_extract`; set the env off to disable instantly). No schema change.
- The isolated call caps at the first 5 sub-document pages; a DOI stated only on a later page is
  conservatively omitted ("leave it alone").
- Rollback: revert the PR / set `SUMMARY_DOI_EXTRACT=false`. The backfill rewrites stored DOI
  prefixes one-way; re-runnable. Run the backfill on Sarhad only after deploy, on a fresh go.
