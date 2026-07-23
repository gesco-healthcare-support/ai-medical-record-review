---
feature: Surface which sub-documents failed to summarize (list + highlight) so the user can act
date: 2026-07-22
status: draft
base-branch: main
folds-into: 2026-07-22-persistent-header (same branch feat/persistent-header, same PR)
related-issues: []
---

## Goal

When a summarize run ends `needs_attention` ("N of M documents could not be summarized"), the app
must tell the user EXACTLY which sub-documents failed and why -- by (a) LISTING each failed row
(page range + title + reason) in the notice, and (b) HIGHLIGHTING those rows in the Review & correct
table -- so the user can find them to exclude/fix, or report the exact rows to the developer. Today
the message is generic and names nothing, so the failure is un-actionable.

## Context & decisions

Live evidence (server 2026-07-22): alocker's RAMIREZ record (doc 4acdccba, 60 included rows) ended
`needs_attention`; 59 summarized, 1 did not -- row idx 50, pages 93-93 ("Laboratory Report"),
`EmptyExtractionError: no OCR text for pages 93-93` (a scanned bilevel image with no readable text).
The user only saw "1 of 60 document could not be summarized. Review, correct, or exclude them, then
summarize again." -- no page, no reason -> stuck.

KEY FINDING (already-captured data): the worker already builds `attention_rows = [{idx, pages,
reason}]` and persists it in `job.attention = {"rows": [...], "message": ...}`
([tasks.py:320,354,155](backend/app/worker/tasks.py#L320)). The ONLY reason the browser never sees
it: `Job.progress()` ([models.py:231](backend/app/models.py#L231)) -- the body of
`GET /documents/{id}/status` -- returns just {kind,state,stage,current,total,error} and drops
`attention`. So this hotfix is mostly "stop dropping data we already have," plus UI to show it.

Decisions (per Adrian, 2026-07-22):
- HOTFIX folded into the current change set (branch `feat/persistent-header`, one PR) for fastest
  deploy. This plan is verified BEFORE building; build only on Adrian's explicit go.
- Must LIST **and** HIGHLIGHT the problematic rows (both, not either).
- Immediate unblock for alocker is separate (he excludes page 93 in the UI himself; I do not touch
  his account). See [[mrr-ai-alocker-account-hands-off]].

Constraints: RUNTIME change -> server redeploy after merge. NO schema change (the `attention` JSON
column already exists, migrated). Reason wording is `EmptyExtractionError.user_message`
([errors.py:42](backend/app/errors.py#L42)) via `reason_for`.

## All needed context

- `Job.attention` JSON col EXISTS ([models.py:222](backend/app/models.py#L222)); `_finalize_needs_attention`
  writes `{"rows": sig.rows, "message": sig.message}` ([tasks.py:149-161](backend/app/worker/tasks.py#L149)).
  `attention_rows` items are `{"idx": <position among included rows>, "pages": "<start>-<end>",
  "reason": <user_facing_message>}` ([tasks.py:354-360](backend/app/worker/tasks.py#L354)).
- `Job.progress()` ([models.py:231-239](backend/app/models.py#L231)) -> the `job` field of
  `GET /documents/{id}/status` ([documents.py:323-331](backend/app/api/documents.py#L323)). Drops `attention`.
- FE `JobProgress` type ([types.ts:24-31](frontend/lib/types.ts#L24)) -> `getStatus`
  ([review-api.ts:16-20](frontend/lib/review-api.ts#L16)).
- `useReviewWorkflow` ([use-review-workflow.ts](frontend/hooks/use-review-workflow.ts)): `attention`
  state is `{message} | null` (:73); set in `watchSummarize` needs_attention (:166-172) and on boot
  recovery (:223-230); `pollJob` returns `PollResult {outcome, message?}` (:32) reading `snap.job`
  (:111). `onSummarize`/`onStart` clear attention (:272,287).
- `ReviewPageClient` ([review-page-client.tsx](frontend/components/review/review-page-client.tsx)):
  renders the attention notice (:140-144, `wf.attention.message`); renders `<ReviewEditor rows=...>`
  (:176-184); tab state + the summaries-follow effect (:31-34).
- `ReviewEditor` ([review-editor.tsx:203-222](frontend/components/review/review-editor.tsx)) computes
  `errors` and passes them to `RowsTable`.
- `RowsTable` ([rows-table.tsx](frontend/components/review/rows-table.tsx)): applies `errors.has(i) &&
  "invalid"` to the second (fields) row (:211-217); the title row is :97-104. Row key for matching a
  failure = `${row.start}-${row.end}` (unique; overlaps are rejected by validation).
- CSS: the review editor styles live with the app CSS (`.doc-row.invalid` etc.); the highlight class
  mirrors that (locate the exact file at build; likely `frontend/app/globals.css` or a review scss).

## Tasks (implementation blueprint)

### H1 - Backend: expose attention on the job status payload  [approach: test-after]
- what: MODIFY `Job.progress()` ([models.py:231](backend/app/models.py#L231)) to add
  `"attention": self.attention` (a dict `{rows, message}` or None). No worker change (already writes
  it), no migration (column exists).
- acceptance (EARS): WHEN a summarize job has ended needs_attention, THE `job` object from
  GET /documents/{id}/status SHALL include `attention.rows` (each with idx, pages, reason). WHERE no
  attention was recorded, `attention` SHALL be null.

### H2 - Frontend types: JobProgress carries attention  [approach: code]
- what: MODIFY [types.ts](frontend/lib/types.ts) - add `export type FailedRow = { idx: number;
  pages: string; reason: string }` and `export type JobAttention = { message: string; rows:
  FailedRow[] }`; add `attention?: JobAttention | null` to `JobProgress`.
- acceptance (EARS): WHEN typechecking, THE status payload's `job.attention.rows` SHALL be typed.

### H3 - Frontend workflow: carry failed rows into attention state  [approach: test-after]
- what: MODIFY [use-review-workflow.ts](frontend/hooks/use-review-workflow.ts):
  - `attention` state type -> `{ message: string; rows: FailedRow[] } | null`.
  - `PollResult` -> add `rows?: FailedRow[]`; in the needs_attention resolve (:124-128) include
    `rows: job.attention?.rows ?? []`.
  - `watchSummarize` needs_attention (:166-172): `setAttention({ message: result.message || "...",
    rows: result.rows ?? [] })`.
  - boot recovery (:223-230): `setAttention({ message: snap.job?.error || "...", rows:
    snap.job?.attention?.rows ?? [] })`.
- acceptance (EARS): WHEN a summarize run ends needs_attention (live or recovered on boot),
  THE `attention.rows` SHALL list the failed sub-documents (pages + reason).

### H4 - Frontend UI: list + highlight the problematic rows  [approach: test-after]
- what:
  - MODIFY `review-page-client.tsx`: derive `attentionPages = new Set((wf.attention?.rows ??
    []).map(r => r.pages))` and a `${start}-${end} -> title` map from `wf.rows`. Render the notice
    (:140-144) as the message + a list: each failed row = `Page {pages}{title ? " - " + title : ""}:
    {reason}`. When `wf.attention` is set, force the Review tab active (extend the effect at :31-34)
    so the highlights are visible. Pass `attentionPages` to `<ReviewEditor>`.
  - MODIFY `review-editor.tsx`: accept `attentionPages?: Set<string>` and forward to `RowsTable`.
  - MODIFY `rows-table.tsx`: add an `attention` class to a row when
    `attentionPages?.has(`${row.start}-${row.end}`)` (both the title row :97 and the fields row :211),
    and render a small inline "Could not summarize" marker on the title row for those rows.
  - ADD CSS: a calm warning highlight (amber left-border/background), mirroring `.doc-row.invalid`.
- acceptance (EARS): WHEN a summarize run ends needs_attention, THE editor SHALL highlight each
  failed sub-document row AND the notice SHALL list each (page range + title + reason). WHEN the user
  re-runs summarize (or re-segments), THE highlight + list SHALL clear (attention reset already at
  :272,287).

### H5 - Tests  [approach: test-after]
- backend: run `summarize_document(job_id)` with `summarize_row` mocked to raise
  `EmptyExtractionError` for one included row and succeed for the rest; assert the job ends
  `needs_attention`, `job.attention["rows"]` contains that row's `pages` + reason, and
  `job.progress()["attention"]["rows"]` exposes it. Mirror the summarize worker-task test pattern in
  backend/tests (locate exact file at build).
- frontend: (1) review-page-client render test (mock `useReviewWorkflow` to return an editor section
  with `attention.rows=[{pages:"93-93",reason:"...",idx:...}]` and a matching row) -> asserts the
  notice lists "Page 93-93 ... : ..." AND the matching row carries the `attention` class; mirror the
  existing review-page-client test. (2) extend a workflow-level assertion that needs_attention
  populates `attention.rows` if a hook test exists, else fold into the component test.
- falsifiability: each new assertion proven to fail under a deliberate mutation (drop `attention`
  from progress(); drop the highlight class; empty the list).

## Validation loop
- `cd frontend && pnpm test && pnpm typecheck`.
- `cd backend && uv run ruff check . && uv run ruff format --check .` (worker test runs in CI / dev DB).
- Open in the SAME PR as the persistent-header change; watch all CI jobs green + SonarCloud gate.
- After merge: server redeploy (git pull + `docker compose build api web` + `up -d`) - runtime change.

## Risk / rollback
Blast radius: Job.progress() (one added key, additive - other consumers ignore it),
use-review-workflow attention state, review-page-client + review-editor + rows-table + a CSS rule.
No schema change, no migration, no worker-logic change. Additive payload field = backward safe.
Rollback: revert the squash PR (the whole change set).
Watch-out: failed rows are matched to editor rows by `${start}-${end}`; if the user EDITS a failed
row's page range before re-running, the highlight for that row may not match until the next run -
acceptable (the list still shows the original pages; re-summarize clears attention). The stored
`attention.idx` is the position among INCLUDED rows at run time, not review_row.idx - so match on
`pages`, never on idx.
