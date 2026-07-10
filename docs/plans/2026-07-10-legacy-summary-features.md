---
feature: legacy-summary-features
date: 2026-07-10
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Add inline per-summary re-run to the Summaries step, and two standalone document workspaces
(/diagnostics = categories 3+8, /depositions = category 9) that upload-or-pick a document,
review/correct it inline via the shared editor, then extract a combined PDF and produce a
filtered Word summary of just those records.

## Context
The old monolith had three extra summary-related screens the new Evaluators app dropped:
an individual-record summarizer and two "reports builder" pages (Diagnostic & Operative,
Depositions) that bundled category-matching pages into a PDF. Adrian wants those back, on
the new account-based/db-backed architecture, plus an inline re-run control on summaries.

Decisions locked with Adrian (research 2026-07-10):
- B re-run: SYNCHRONOUS; fresh output; clears that row's hand-edits (confirm if edits exist).
- C/D scope: PER-DOCUMENT (one uploaded record's rows), not cross-document/case.
- C/D placement: STANDALONE pages (/diagnostics, /depositions).
- C/D pipeline: pages accept NEW uploads AND existing docs; a not-yet-reviewed doc is
  segmented + corrected INLINE by EMBEDDING the shared review editor.
- C/D summarize: FRESH summaries of matched rows -> filtered Word doc (works even if the
  full document summary was never run).

Constraints: HIPAA - all extraction/export IN-MEMORY or short-lived per-user artifacts, no
~/MRRs, ids-only logging, LLM only on the Vertex/BAA path. job_queue enforces one active job
per document. The classic-UI blueprints (summarize.py, legacy export routes, state.py) stay
untouched. Diffs are additive except the review.js/review.html refactor, which is the only
change to working code and must be regression-verified.

## Approach
Reuse over rebuild. The new app already has upload (`create_document`), listing
(`list_documents`), segmentation (`segment/start` + `run_segmentation`), the review editor,
`summarize_engine.summarize_row`, and the in-memory Word builder
(`export._build_mrr_document`). Every feature is composed from these.

The one real refactor: the identify + Review-&-correct steps live inside a monolithic,
global-state, DOM-id-bound `review.js`. To embed them on the bundle pages we extract those
steps into a component mountable by (root element, document_id) that emits a "reviewed"
state. The main /review page keeps mounting it (plus its own Summaries step); bundle pages
mount it (plus their own category-bundle step). This is staged FIRST and verified against
the untouched main flow before anything depends on it.

C and D are ONE generalized, category-parameterized backend + one shared JS module; the two
pages differ only by category set and labels (DRY - they are the same feature).

Bundle "Summarize" is kept SYNCHRONOUS but bounded (in-memory, matches the existing /export
pattern, no artifact storage): matched rows are summarized on the spot with a spinner, and
above a configurable cap the UI directs the user to the main Summaries flow instead.
- Alternative rejected - async job + stored .docx artifact: fits long runs but reintroduces
  on-disk PHI artifacts and collides with the per-document Summary set / one-job-per-doc rule.
  Revisit only if real diagnostic/deposition bundles routinely exceed the cap.
- Alternative rejected - reuse persisted summaries then filter: Adrian chose fresh/subset so
  it works without a prior full run.

## Tasks

- T1: Extract the identify + Review-&-correct steps from review.js/review.html into a
  reusable editor component mountable by (rootEl, documentId), emitting a "reviewed" event +
  current rows; preserve autosave, job polling, validation, merge-suggestion apply, and
  add/split-row. Re-point the main /review page at the component (it keeps its Summaries
  step). No behavior change to the main flow.
  - approach: test-after
  - files-touched: [mrr_ai/static/review.js, mrr_ai/templates/review.html, mrr_ai/static/doc-editor.js (new), mrr_ai/static/doc-editor.css (maybe)]
  - acceptance: on /review, segment -> correct (split/add/merge-apply/validate) -> autosave
    -> summarize all behave identically to pre-refactor (browser-verified, no console errors);
    existing pytest suite still green.

- T2: Generalized category-bundle backend. Pure helpers (unit-tested): filter ReviewRows by
  a category set, collect their page ranges, assemble filtered summary entries. Endpoints:
  POST /api/documents/<id>/bundle/pdf {categories:[...]} -> pypdf-concat matched pages
  IN-MEMORY -> send_file; POST /api/documents/<id>/bundle/summarize {categories:[...]} ->
  summarize_row over matched rows (bounded by cap) -> _build_mrr_document -> send_file .docx.
  Owner-checked (_own); ids-only logging; 409 if an active job conflicts.
  - approach: tdd (pure helpers) + test-after (endpoints)
  - files-touched: [mrr_ai/services/bundles.py (new), mrr_ai/blueprints/documents_api.py, tests/unit/test_bundles.py (new), tests/unit/test_documents_api.py]
  - acceptance: bundle/pdf returns a PDF containing exactly the matched rows' pages in order;
    bundle/summarize returns a .docx of only matched-row summaries; non-owner -> 404; over-cap
    -> 409 with a "use the main Summaries flow" message; unit tests green.

- T3: /diagnostics (cats {3,8}) and /depositions (cat {9}) pages: one shared JS module +
  Evaluators shell, parameterized by category set + labels. Upload area (create_document) +
  existing-document list (list_documents) with status; selecting a not-yet-reviewed doc
  mounts the T1 editor to segment + correct inline; once reviewed, the bundle actions
  (Extract combined PDF / Summarize -> Word) enable. New blueprint routes serve the two pages.
  - approach: test-after
  - files-touched: [mrr_ai/templates/diagnostics.html (new), mrr_ai/templates/depositions.html (new), mrr_ai/static/bundle-page.js (new), mrr_ai/blueprints/pages.py (or a new blueprint), mrr_ai/static/evaluators.css (maybe)]
  - acceptance: from /diagnostics, upload a new PDF OR pick an existing one; if unreviewed,
    segment + correct inline; then download a combined cats-3+8 PDF and a filtered Word
    summary; /depositions does the same for cat 9; browser-verified end-to-end on a synthetic
    case; ownership enforced.

- T4: Feature B - inline re-run. Endpoint POST /api/documents/<id>/summaries/<idx>/resummarize:
  owner-checked; 409 if an active summarize job; map the Summary to its ReviewRow (start/end/
  category from the snapshot, injury_date/flag/date from the ReviewRow) -> summarize_row ->
  replace raw title/date/text/source_text and CLEAR edited_* -> return summary.listing().
  UI: a "Re-run" button per summary card in the (refactored) summaries step; confirm if the
  card has edits; spinner on that card; re-render on return.
  - approach: test-after (unit-test row-mapping + edit-clearing with summarize_row mocked)
  - files-touched: [mrr_ai/blueprints/documents_api.py, mrr_ai/static/review.js, mrr_ai/templates/review.html, tests/unit/test_documents_api.py]
  - acceptance: clicking Re-run re-summarizes that one row, clears its prior edits (after a
    confirm when edits exist), and updates only that card; endpoint 409s mid-summarize-job;
    unit tests green.

## Risk / Rollback
- Blast radius: T1 touches the working review editor (highest risk - a regression breaks the
  main flow). T2-T4 are additive (new files/endpoints/pages); classic-UI blueprints untouched.
- Large-PDF page extraction holds pages in memory (pypdf) - bounded by per-document size.
- Sync bundle-summarize latency - mitigated by the cap + spinner + over-cap fallback.
- Concurrency - all new mutating paths respect the one-active-job-per-document guard.
- Rollback: revert the branch. T1 is landed + verified as its own commit first, so it can be
  reverted independently if it regresses the main flow. No schema changes (no migration).

## Verification
After all tasks, on a fresh synthetic case (browser, dev server):
1. /review: full identify -> correct -> summarize -> export still works unchanged (T1 guard).
2. Summaries step: edit a summary, then Re-run it -> confirm prompt -> fresh text, edit
   cleared, other cards untouched (T4).
3. /diagnostics: upload a NEW synthetic PDF -> segment + correct inline -> Extract PDF
   (contains only cat 3/8 pages) -> Summarize (Word doc of only cat 3/8 records) (T3+T2).
4. /diagnostics: pick an ALREADY-reviewed document -> bundle actions enable immediately.
5. /depositions: same for cat 9.
6. Ownership: a second account cannot reach another user's document via any new route (404).
7. pytest full suite green; ruff clean; pyright no new errors on touched files.
