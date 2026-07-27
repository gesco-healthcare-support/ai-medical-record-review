---
feature: Correct the DOI on existing summaries (scoped backfill) and stop truncating multi-date DOIs
date: 2026-07-27
status: in-progress
base-branch: main
related-issues: []
---

## Goal

Existing summaries and their Word/PDF exports show a date of injury only when that sub-document
states one - and when a document states more than one, every stated date survives to the page and the
export - with a backfill that can be scoped to one user or record so another account's data is never
touched.

## Context & decisions

Why now: PR #40 fixed DOI extraction for NEWLY generated summaries only. `summarize_document` REUSES
a Summary whose `(start, end, category)` is unchanged (`backend/app/worker/tasks.py:381-405`; only
"Re-summarize all" clears first, `documents.py:572-577`), so pre-#40 summaries keep the propagated
claim DOI on the Summaries page and in both exports.

Established by reading the code (do NOT "fix" these):
- The export re-applies the DOI from the RAW `summary.text` (`documents.py:717-721`) because the
  Summaries UI STRIPS the prefix into its edit box (`summaries-view.tsx:24-28`), so a reviewer-saved
  `edited_text` never contains it. The re-apply is correct; the stale prefix is bad data.
- `summarize_row` stamps the prefix on BOTH `summaryText` and `verifiedText`
  (`summarize_engine.py:109,122,129`), so a verified summary needs no restoration.
- Per-summary "Re-draft" already re-extracts the DOI in isolation (`documents.py:676`), so a
  one-off correction path exists today.

Resolved decisions:
- Decision: `backfill_doi.py` gains `--user-email`, `--document-id` (repeatable) and `--all`, and
  REFUSES to run without exactly one of them, because the current global default would rewrite
  alocker@socalpm.com's summaries on the shared server - a hard boundary - and an explicit scope
  makes the blast radius reviewable before the run.
- Decision: the script resolves and PRINTS the document ids in scope before touching anything, so a
  `--dry-run` shows exactly which records would change.
- Decision: the multi-date grammar lives in ONE place, a new `doi_prefix()` in
  `app/services/summary_doi.py` reusing the existing `_DOI_PREFIX` pattern, because the export's ad
  hoc `\*\*DOI\*\*:[^,]*,` (`documents.py:718`) stops at the first comma and silently drops the
  second stated date that `_clean` can emit (`summary_doi.py:41-48`).
- Decision: the frontend regex (`summaries-view.tsx:24`) is widened to the same grammar rather than
  moved to the API, keeping this change small; it is documented as a mirror of `_DOI_PREFIX`.
- Decision: NOT in scope - showing a DOI chip for a reviewer-edited summary (the FE only receives the
  effective text, which by design no longer holds the prefix). Cosmetic, unchanged by this work, and
  it would add a new API field.

## All needed context

Re-planned against main `bd4f1b3` (after #43/#44/#45). Anchors re-verified; the only shifts are line
numbers, listed below. #45 restructured the summary CARD markup (title and meta are now buttons) but
left `parseDisplay` and the `doi` chip untouched, so this change is unaffected. Live confirmation of
both halves seen while verifying #44 on a real record: its rows carry `12/01/2024, 04/21/2025` as the
injury date while the summary chips show only `DOI 12/01/2024` (truncation), and every summary carries
a claim-level DOI that the sub-document itself does not state (stale pre-#40 data).

- `app/services/summary_doi.py`: `_DOI_PREFIX` at :38 (`^\s*\*\*DOI\*\*:\s*\d[\d/.\-]*(?:\s*,\s*\d[\d/.\-]*)*\s*,\s*`),
  `_clean` (multi-date join) at :41-48, `apply_doi_prefix` at :81-93 (uses `_DOI_PREFIX.sub`).
- `_export_title_and_text` (`backend/app/api/documents.py:713-727`), consumed by `_export_entry`
  (:730-738) and `_pdf_entry` (:741-750) - the Word and linked-PDF paths.
- `parseDisplay` (`frontend/components/review/summaries-view.tsx:19-32`, regex at :26) - strips the prefix and
  surfaces it as the `DOI x` chip in the card meta line (:166-174).
- `backend/scripts/backfill_doi.py`: `run(session, dry_run)` at :31-58 iterates EVERY `Summary`;
  `main()` argparse at :61-68. `Document.user_id` links a record to its owner
  (`backend/app/models.py`), `User.email` is the FastAPI-Users column.
- Test patterns: `backend/tests/test_summary_doi.py` (pure unit tests for this module),
  `with get_sessionmaker()() as session:` for DB-touching tests (e.g.
  `backend/tests/test_documents_api.py:38`), `seeded_user` + `authed` fixtures in
  `backend/tests/conftest.py:85-105`.
- Sonar note: `sonar.sources` covers `backend/app` only, so `backend/scripts/**` is outside the
  coverage gate - the script still gets a test because it WRITES data under a hard boundary.

## Tasks (implementation blueprint)

### Task 1 - single-source DOI prefix helper
- what: MODIFY `backend/app/services/summary_doi.py`: wrap the date list in `_DOI_PREFIX` in a
  capture group and add
  `def doi_prefix(body) -> str` returning `f"{match.group(1)},"` when the pattern matches the start of
  `body`, else `""`. Docstring: it returns the stored prefix INCLUDING its trailing comma, and is the
  one grammar shared by `apply_doi_prefix` and the export.
- pattern: `apply_doi_prefix` at `summary_doi.py:81-93`.
- approach: tdd
- acceptance (EARS):
  - WHEN a body starts with `**DOI**:05/08/2022, rest`, THE SYSTEM SHALL return `**DOI**:05/08/2022,`.
  - WHEN a body starts with `**DOI**:05/08/2022, 06/01/2023, rest`, THE SYSTEM SHALL return
    `**DOI**:05/08/2022, 06/01/2023,`.
  - WHEN a body has no DOI prefix, THE SYSTEM SHALL return an empty string.
  - WHEN a body contains `**DOI**:` other than at the start, THE SYSTEM SHALL return an empty string.

### Task 2 - export keeps every stated date
- what: MODIFY `backend/app/api/documents.py` `_export_title_and_text`: import `doi_prefix` from
  `app.services.summary_doi` and replace the local `re.match(...)` at :724 with
  `prefix = doi_prefix(summary.text)` / `if prefix and "**DOI**" not in text: text = f"{prefix} {text}"`.
- pattern: the current block at `documents.py:723-727`.
- approach: tdd
- acceptance (EARS):
  - WHEN a summary's raw text carries a two-date DOI prefix and the reviewer's edited text has none,
    THE SYSTEM SHALL prepend BOTH dates to the exported body.
  - WHEN the effective text already contains a DOI prefix, THE SYSTEM SHALL NOT prepend a second one.
  - WHEN the raw text has no DOI prefix, THE SYSTEM SHALL export the effective text unchanged.

### Task 3 - Summaries page shows every stated date
- what: MODIFY `frontend/components/review/summaries-view.tsx` `parseDisplay`: replace the DOI regex
  with `/^\s*\*\*DOI\*\*:\s*([\d/.\-]{4,}(?:\s*,\s*[\d/.\-]{4,})*)\s*,\s*/` and keep the existing
  strip-and-chip behaviour, so the chip reads `DOI 05/08/2022, 06/01/2023`. Comment it as the mirror
  of `_DOI_PREFIX` in `app/services/summary_doi.py`.
- pattern: the current `parseDisplay` at `summaries-view.tsx:19-32`.
- approach: test-after
- acceptance (EARS):
  - WHEN a summary body carries a two-date DOI prefix, THE SYSTEM SHALL show both dates in the card
    meta line and SHALL NOT render the prefix inside the summary body.
  - WHEN a summary body has no DOI prefix, THE SYSTEM SHALL show no DOI chip.

### Task 4 - scoped, auditable backfill
- what: MODIFY `backend/scripts/backfill_doi.py`: add `--user-email`, `--document-id` (action
  `append`), `--all`, keep `--dry-run`; add
  `def scoped_document_ids(session, user_email=None, document_ids=None, every=False) -> list[str]`
  (email -> that user's document ids via `Document.user_id`, matched case-insensitively on
  `User.email`; ids -> validated against `Document`; `every` -> all ids) which raises
  `SystemExit` with a clear message when no scope is given, when the email matches no user, or when a
  given document id does not exist; change `run(session, document_ids, dry_run)` to iterate
  `Summary` filtered by those ids; print the scope line
  (`backfill_doi: scope <n> document(s): <ids>`) BEFORE any extraction, then the changed count.
- pattern: the existing `run` + `main` at `backfill_doi.py:31-68`.
- approach: tdd
- acceptance (EARS):
  - IF no scope flag is given, THEN THE SYSTEM SHALL exit with an error and change nothing.
  - WHEN `--user-email` names a user, THE SYSTEM SHALL process only that user's documents' summaries.
  - WHEN `--document-id` is given, THE SYSTEM SHALL process only those documents' summaries.
  - WHEN `--dry-run` is given, THE SYSTEM SHALL print the scope and the would-change count and write
    nothing.
  - WHEN run twice with the same scope, THE SYSTEM SHALL report zero changes the second time.

### Task 5 - tests
- what: EXTEND `backend/tests/test_summary_doi.py` for Task 1 (four criteria) and for
  `apply_doi_prefix` round-tripping a two-date prefix; EXTEND `backend/tests/test_documents_api.py`
  with a direct unit test of `_export_title_and_text` for Task 2's three criteria; CREATE
  `backend/tests/test_backfill_doi.py` loading the script via
  `importlib.util.spec_from_file_location` and asserting `scoped_document_ids` for: no scope ->
  `SystemExit`, `--user-email` -> only that user's ids (seed a second user's document and assert it
  is absent), unknown email -> `SystemExit`, unknown document id -> `SystemExit`.
- pattern: existing pure-unit style in `tests/test_summary_doi.py`; `with get_sessionmaker()() as
  session:` DB setup as in `tests/test_documents_api.py:38-60`.
- approach: tdd
- acceptance (EARS): The system shall pass the full backend + frontend suites with new-code coverage
  >= 80%.

## Validation loop

1. BE: `uv run ruff check . && uv run ruff format --check .`
2. BE: `uv run pytest tests/test_summary_doi.py tests/test_backfill_doi.py tests/test_documents_api.py -q`
   then `uv run pytest -q` (the ~5 enqueue/queue-count failures are the known live-RQ-worker drain)
3. FE: `pnpm -C frontend typecheck && pnpm -C frontend exec vitest run`
4. Live (local :8080): `docker compose exec api uv run python scripts/backfill_doi.py --dry-run
   --document-id <a local record>` -> prints the scope + count, writes nothing; run it for real ->
   the Summaries page loses the DOI chip on documents that state no injury date and keeps it (with
   every stated date) where they do; export to Word and confirm the same body text.
5. Deploy step (needs a fresh go): on Sarhad run the same command with
   `--user-email adriang@gesco.com` - never `--all`, so alocker's summaries are untouched.

## Risk / rollback

- Blast radius: the export body for every summary, the Summaries page chip, and - only when the
  script is run - stored `text` / `verified_text` / `edited_text` for the scoped documents.
- The backfill spends one Gemini vision call per summary in scope; it is idempotent and fail-safe
  (an extraction error yields `"-"`, i.e. no prefix).
- The backfill is NOT reversible per-row: the prior prefix is overwritten. Mitigation: `--dry-run`
  first and a narrow scope; the raw model body around the prefix is untouched.
- Rollback: revert the PR for the code paths; re-running the backfill after a revert restores the
  pre-#40 behaviour only by re-extraction, so treat the scoped run as the point of no return.
