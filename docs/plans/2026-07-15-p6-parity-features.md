---
feature: p6-parity-features
date: 2026-07-15
status: in-progress
base-branch: feat/nextjs-fastapi-rewrite
related-issues: []
---

## Goal
Re-create the 3 classic parity features in the modern flow: (1) auto-extract patient name/DOB +
law firm to prefill the report header (via Vertex, not OpenAI); (2) individual-record folders (many
pre-split PDFs processed as one case) with the always-`category_01` bug fixed; (3) aggregated PDF
merge + per-source page ranges.

## Context
Classic sources: `extraction.py` (name/dob/lawfirm via OpenAI), `individual_mrr.py` +
`summarize.py:summarize_indiv_record` (per-record summarize), `reports.py:compute_page_ranges`
(merge + ranges). **The `category_01` bug** is `summarize.py:258` `if option == 1 or "1":` -
`or "1"` is always truthy, so the first branch always runs and EVERY record gets the category_01
prompt. The modern per-category resolution (`catalog.get_prompt(session, "summary", category)`)
fixes it for free.

## Locked decisions (confirmed with Adrian, 2026-07-15)
- **Individual records -> one Document + seeded rows** (not a new case/folder model): multi-file
  upload merges the PDFs into one Document (feature 3's merge), seeding one ReviewRow per source
  file from its page range. Reuses the whole review + summarize + export flow.
- **Auto-classify each record on merge**: a NEW job kind `classify` on the segment (torch) worker
  classifies each seeded row (first-page OCR -> classifier -> category + review flag); the user
  corrects in the review editor. (Not synchronous - the web tier has no torch.)
- **Extraction via Vertex**, one endpoint returning {name, dob, lawfirm}.
- **category_01 fix is automatic** via `catalog.get_prompt` (no buggy if/elif ported).

## Approach
- **Extraction** (`app/services/extraction.py` + `POST /api/documents/{id}/extract-header`):
  OCR the first <=15 pages -> Vertex structured JSON -> {name, dob, lawfirm}; empty OCR -> blanks;
  PipelineError -> the friendly 503/422 (reuse `_pipeline_error_response`).
- **Merge** (`app/services/aggregate.py:merge_pdfs(sources)`): merge `[(name, bytes)]` -> (merged
  bytes, `[{filename,start,end,pages}]`), skipping unreadable files (pypdf, Flask-free).
- **Aggregate upload** (`POST /api/documents/aggregate`, multi-`UploadFile`): merge -> save one
  Document -> seed one ReviewRow per record (page range, title "-", category "100", include True)
  -> enqueue a `classify` job. Returns {id, page_count, records}.
- **classify job**: add kind `classify` to the queues/status maps (routes to the `segment` queue);
  `app/worker/tasks.py:classify_document` classifies each row (first-page OCR best-effort ->
  `classify` -> set category + flag). STATUS_ON_ENQUEUE=segmenting, STATUS_ON_DONE=reviewing.

### Alternatives rejected
- New case/folder model: more faithful but adds a model + a parallel flow; merge-into-one reuses
  everything (Adrian's call).
- Synchronous classify in the web endpoint: the web tier has no torch; classification must run on
  the worker.
- Porting the classic if/elif prompt chain: it carries the category_01 bug; use catalog.get_prompt.

## Tasks
- **P6a - extraction.** approach: test-after.
  - files: app/services/extraction.py, app/api/documents.py (route), tests.
  - acceptance: extract-header returns {name,dob,lawfirm}; empty OCR -> blanks; OCR-unavailable ->
    friendly 503; Vertex mocked in tests.
- **P6b - aggregate merge + classify.** approach: test-after.
  - files: app/services/aggregate.py, app/api/documents.py (aggregate route), app/worker/queues.py
    (+classify), app/services/jobs.py (status maps), app/worker/tasks.py (classify_document), tests.
  - acceptance: merge_pdfs computes correct page ranges + skips unreadable; aggregate creates one
    Document + one ReviewRow per record + enqueues a classify job; classify_document sets each row's
    category (classifier mocked); the category_01 bug does NOT recur (per-category prompt).

## Risk / Rollback
- Blast radius: only `feat/nextjs-fastapi-rewrite`; `main` unaffected.
- Rollback: revert the P6 commits; P1-P5 stand.
- Top risks: (a) classify job OCR-missing -> degrade to title-only (best-effort, like segment's
  escalation), not a hard fail; (b) merge of a huge multi-file case -> memory (in-memory merge; fine
  for typical case sizes, revisit if needed); (c) source filenames may be PHI -> not persisted as
  the row title (title "-"); returned only in the upload response to the owner.

## Verification
`uv run pytest` on docker Postgres + Redis (Vertex + classifier MOCKED). Live extraction/classify
runs verified by Adrian with Vertex ADC, as in prior phases.
