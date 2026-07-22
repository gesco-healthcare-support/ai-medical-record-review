---
feature: App-wide validation + error-handling hardening (never leave a user stuck on a generic error)
date: 2026-07-22
status: in-progress
base-branch: main
related-issues: []
---

## Goal

Across the MRR AI app, a user never hits a dead-end or cryptic error: every failure surfaces as a
clear, actionable message (via one shared humanizer), silent failures (autosave) become loud and
blocking, and the review editor tells users exactly what to fix before summarizing.

## Context & decisions

Why now: a live tester hit a generic "Not found" on Summarize (an ownership 404 surfaced raw). Root
survey showed the UI shows the server's raw error string verbatim, autosave fails quietly, and the
"why is Summarize disabled" reason isn't adjacent. The AI/pipeline errors are already humanized
server-side; the gap is HTTP-level + client presentation. Unlike the CI PR (#31), this DOES change
runtime behavior, so the server must redeploy after merge.

Resolved decisions (modal 2026-07-22):
- Decision: **app-wide** shared `humanizeError` applied to every error-display call-site (review,
  uploads, admin, bundles), because the same raw-message pattern is duplicated in 4 local helpers -
  one helper fixes them all and future sites.
- Decision: **client-side humanizer keyed on HTTP status**, because the 404 is intentionally
  IDOR-vague server-side (must not leak existence) - the client owns turning it into guidance. AI
  errors keep their existing friendly server wording (passed through).
- Decision: **persistent banner + block Summarize** while a save has failed or is pending, because a
  silent autosave failure (the tester's case) must never let someone edit into a void or summarize
  stale rows.
- Decision: **consolidated invalid-rows summary + inline markers + a reason on the disabled button**,
  so users never hunt for what to fix.
- Decision: **fold in all 3 carried findings** - server rejects fractional pages, needs_attention on
  boot routes to editor+notice, StatusPill gets a friendly needs_attention label (closes chip
  task_9a74a425).

## All needed context

Frontend error surface (verified):
- `apiFetch` ([lib/api.ts:16-45](frontend/lib/api.ts)): 401 -> redirect + `ApiError("signed out",401)`.
  Other non-ok -> `ApiError(body.detail ?? body.error ?? "<path> failed (<status>)", status)`. The
  `fetch` call is NOT wrapped, so a network drop throws a raw `TypeError`.
- `ApiError` ([lib/api.ts:6-14](frontend/lib/api.ts)): `{ message, status }`.
- Global 401 redirect + retry-skip 401/403/404 ([providers.tsx:15-45](frontend/app/providers.tsx)).
- Duplicated local error helpers to REPLACE: [bundle-page-client.tsx:29-30](frontend/components/bundle/bundle-page-client.tsx),
  [admin-view.tsx:28-29](frontend/components/admin/admin-view.tsx),
  [documents-view.tsx:22-23](frontend/components/documents/documents-view.tsx),
  [use-review-workflow.ts:34-35](frontend/hooks/use-review-workflow.ts).
- Raw `err.message` display sites: header-bar.tsx:50,64; export-dialog.tsx:81; summaries-view.tsx:84,92,110;
  category-dialog.tsx:69; prompt-dialog.tsx:66; use-review-workflow.ts:145,170,190,266,282,300;
  documents-view.tsx:59,70,78; admin-view.tsx:71,96; bundle-page-client.tsx:89,103,122.
- Review orchestrator ([review-page-client.tsx](frontend/components/review/review-page-client.tsx)):
  `errors = rowErrors(wf.rows, wf.totalPages)` (:36), `included` (:37), save chip (:88-98), Summarize
  `disabled={errors.size > 0 || included === 0}` (:112), banner (:126), attention notice (:127-131).
- Autosave ([use-review-workflow.ts:287-304](frontend/hooks/use-review-workflow.ts)): sets `saveState`
  "dirty" immediately; debounced save skips silently when `rowErrors(...).size` (invalid) - the chip
  stays "dirty" without saying why.
- needs_attention boot bug ([use-review-workflow.ts:150-174](frontend/hooks/use-review-workflow.ts)):
  `watchSummarize` reads the stale `rows` closure (`[]` at boot), so a boot-active summarize ending
  needs_attention routes to `showStart` instead of `enterEditor`.
- StatusPill maps ([status-pill.tsx:5-22](frontend/components/documents/status-pill.tsx)): no
  `needs_attention` entry -> raw label + neutral tone.

Backend error surface (verified):
- Terse HTTP details surfaced raw: `deps.py:25` 404 "not found" (IDOR - keep vague); documents.py
  409 "a job is already running..." (368,408), 400 "no rows are marked for summarization" (390),
  validate_rows strings (348,388); admin.py 400/404/409 (62-176). AI/pipeline errors already friendly
  via `_pipeline_error_response` ([documents.py:63-74](backend/app/api/documents.py)) + `errors.py`.
- `validate_rows` ([rows.py:14-32](backend/app/services/rows.py)): `int(row["start"])` truncates a
  JSON float 3.5 -> 3 silently (client `rowErrors` rejects it) - the divergence to close.

Gotchas: 404 detail MUST stay "not found" server-side (IDOR); do the friendly wording client-side.
This is a RUNTIME change -> server redeploy after merge (pull main + `docker compose build` + up).
CI (PR #31) now runs frontend Vitest + backend pytest + Playwright E2E - new tests are enforced.

## Tasks (implementation blueprint)

### T1 - Backend: reject non-integer-valued pages  [approach: tdd]
- what: MODIFY [rows.py:20-29](backend/app/services/rows.py) `validate_rows` - replace the
  `int(row["start"]), int(row["end"])` coercion with a strict `_as_int(v)` helper that accepts an int,
  an integer-valued float (`float.is_integer()`), or a clean integer string, and raises `ValueError`
  otherwise. A fractional float (3.5) or "3.5" -> the existing "row N: start/end must be integers".
- pattern: the current try/except at rows.py:21-24.
- approach: tdd.
- acceptance (EARS): IF a row's start or end is a non-integer number or non-integer string, THEN
  validate_rows SHALL return "row {i}: start/end must be integers" and SHALL NOT silently truncate.
  WHEN start/end are integer-valued, THE existing behavior SHALL be unchanged.

### T2 - Frontend: network-safe apiFetch + shared humanizeError  [approach: tdd]
- what: MODIFY [lib/api.ts](frontend/lib/api.ts) - wrap the `fetch(...)` in try/catch; on a transport
  failure throw `new ApiError("network", 0)`. CREATE `frontend/lib/errors.ts` exporting
  `humanizeError(err: unknown, ctx?: { notFound?: string; fallback?: string }): string`:
  - non-ApiError OR status 0 -> "Couldn't reach the server. Check your connection and try again."
  - 401 -> "Your session has ended. Please sign in again."
  - 403 -> "You don't have permission to do that."
  - 404 -> `ctx?.notFound ?? "This item is no longer available - it may have been deleted or moved. Refresh and try again."`
  - status >= 500 OR message matching `/failed \(\d+\)$/` (apiFetch's synthetic fallback) ->
    `ctx?.fallback ?? "Something went wrong on our end. Please try again; contact your administrator if it keeps failing."`
  - else (400/409/422/503 carrying a real server detail) -> `err.message`.
- pattern: lib/api.ts:38-42; replaces the 4 local helpers listed in context.
- approach: tdd (pure function).
- acceptance (EARS): WHEN the server returns 404, THE humanizeError SHALL return the friendly (ctx)
  message, never "not found". WHEN fetch fails at the network layer, THE apiFetch SHALL throw
  ApiError(status 0) and humanizeError SHALL return the connection message. WHERE a 400/409 carries an
  actionable server detail, humanizeError SHALL preserve it. WHEN a 500 carries no friendly body, THE
  humanizeError SHALL return the generic server message (not "<path> failed (500)").

### T3 - Apply humanizeError at every call-site (remove local helpers)  [approach: test-after]
- what: MODIFY all display sites in the context list to call `humanizeError(err, ctx)` with a
  context-appropriate `notFound`/`fallback` (e.g. review: notFound "This record is no longer available
  to you - it may have been moved or deleted. Go back and refresh."). DELETE the 4 local
  `errMessage`/`message` helpers. Keep register-form's explicit 400 handling.
- pattern: the local helpers being removed; each catch block already has the shape.
- approach: test-after.
- acceptance (EARS): WHEN any humanized action fails, THE UI SHALL show the humanized message; a repo
  grep SHALL find no local `errMessage`/`message(err` helper outside `lib/errors.ts`.

### T4 - Autosave: loud + blocking  [approach: test-after]
- what: MODIFY [use-review-workflow.ts:287-304](frontend/hooks/use-review-workflow.ts) `onRowsChange`
  - when the debounced set is invalid (rowErrors non-empty), set `saveState = { kind: "error", message:
  "Not saved - fix the highlighted rows first" }` (was: left "dirty"); on a save request failure set
  `{ kind: "error", message: humanizeError(err, ...) }`. MODIFY
  [review-page-client.tsx](frontend/components/review/review-page-client.tsx): render a persistent
  warning banner while `save.kind === "error"`; extend the Summarize `disabled` (:112) to also disable
  while `save.kind === "error" || save.kind === "dirty"`.
- pattern: review-page-client.tsx:88-98 (chip), :112 (disabled), :126 (banner div).
- approach: test-after.
- acceptance (EARS): WHILE the row set is invalid OR a save has failed, THE SYSTEM SHALL show a
  persistent "changes not saved" warning AND keep Summarize disabled. WHEN a valid save succeeds, THE
  warning SHALL clear and Summarize SHALL re-enable (given valid + >=1 included).

### T5 - Consolidated invalid-rows reason at the action  [approach: test-after]
- what: MODIFY [review-page-client.tsx](frontend/components/review/review-page-client.tsx) - when
  Summarize is disabled, render an itemized reason: IF `errors.size > 0` list "Fix before summarizing:"
  + one line per `[index,msg]` in `errors` as "Document {index+1}: {msg}"; ELSE IF `included === 0`
  "Select at least one document to summarize."; ELSE the autosave message. Add a `title` on the
  disabled button summarizing the first reason.
- pattern: review-page-client.tsx:36 (errors), :109-118 (button); rowErrors returns Map<index,msg>.
- approach: test-after.
- acceptance (EARS): WHEN Summarize is disabled due to invalid rows, THE SYSTEM SHALL display an
  itemized list naming each offending document + its issue; WHEN disabled due to 0 included, THE SYSTEM
  SHALL instruct the user to select at least one document.

### T6 - needs_attention boot routing fix  [approach: test-after]
- what: MODIFY [use-review-workflow.ts](frontend/hooks/use-review-workflow.ts) - add
  `const rowsRef = useRef<EditorRow[]>([])`, keep it in sync wherever `setRows` is called, and in
  `watchSummarize` (:160,:166) use `rowsRef.current.length` instead of the stale `rows` closure. So a
  boot-active summarize ending needs_attention routes to the editor + notice.
- pattern: use-review-workflow.ts:50 (rows state), :150-174 (watchSummarize).
- approach: test-after.
- acceptance (EARS): WHEN a summarize job active at boot ends needs_attention and the document has
  rows, THE SYSTEM SHALL open the editor with the attention notice, not the start panel.

### T7 - StatusPill needs_attention label  [approach: test-after]
- what: MODIFY [status-pill.tsx:5-22](frontend/components/documents/status-pill.tsx) - add
  `needs_attention: "Needs attention"` to STATUS_LABELS and `needs_attention: "warning"` to
  STATUS_TONES. Then withdraw chip `task_9a74a425` (dismiss_task).
- pattern: the existing map entries.
- approach: test-after.
- acceptance (EARS): WHEN doc.status is needs_attention, THE StatusPill SHALL render "Needs attention"
  + the `hd-badge-warning` tone.

### T8 - Tests for the new behavior  [approach: test-after]
- what: CREATE `frontend/lib/errors.test.ts` (humanizeError: network, 401, 403, 404+ctx, 500/fallback,
  400/409 passthrough). EXTEND: `use-review-workflow.test.tsx` (invalid rows -> saveState error + no
  save; boot-active summarize needs_attention -> editor); `status-pill.test.tsx` (needs_attention);
  `backend/tests/test_rows_property.py` + a `rows` example test (fractional page rejected).
- pattern: the existing suites from PR #31.
- approach: test-after (T1/T2 are tdd, their tests come first there).
- acceptance (EARS): WHEN `pnpm test` and the backend pytest run, THE new suites SHALL pass; each new
  assertion SHALL be shown to fail under a deliberate mutation (falsifiability).

## Validation loop

- Backend: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest`
  (validate_rows fractional test; full suite - needs the dev DB, else CI).
- Frontend: `cd frontend && pnpm test && pnpm typecheck` (humanizeError + hook + status-pill suites;
  build is CI-authoritative on Windows).
- Manual falsifiability spot-check on humanizeError (mutate a branch, see red).
- E2E (CI): the existing Playwright specs still green.
- CI (authoritative): push branch, open PR into main, watch all jobs green + SonarCloud new-code gate.
- Then: server redeploy (pull main + `docker compose build` + `up -d`) - this changes runtime UX.

## Build notes (2026-07-22)

- Deviation from T3: the local `errMessage`/`message` helpers were kept as thin
  `humanizeError(err, { fallback, notFound })` wrappers (context-bound per file) rather than
  deleted - this removes the duplicated `instanceof ApiError` logic (the actual goal) with fewer,
  lower-risk edits. No component contains humanization logic anymore; it all lives in `lib/errors.ts`.
- Two extra call-sites the initial grep missed were also converted: `summaries-view.tsx` load-error
  and `split-upload-dialog.tsx` (aggregate). Grep now finds no `instanceof ApiError` outside
  `lib/errors.ts`, `providers.tsx` (401 routing), and `register-form.tsx` (explicit 400 handling).
- Verified: 58 frontend tests + typecheck clean; backend ruff clean + `validate_rows` fractional
  reject confirmed by direct call; falsifiability confirmed on the 404 humanizer (mutation -> 2 red,
  reverted). Backend pytest + E2E run in CI.

## Risk / rollback

Blast radius: broad frontend diff (many display sites) but all funnel through one tested humanizer;
the backend change is a small, tested tightening of validate_rows. Unlike PR #31 this DOES change
runtime behavior (error copy, disabled-Summarize gating, validation) - low risk (messaging + gating),
but the server must redeploy to pick it up.
Rollback: revert the squash PR (restores all call-sites + validate_rows). No schema change, no
migration.
Watch-out: over-blocking Summarize (T4) could frustrate if `dirty` lingers - ensure a successful save
clears it promptly (the 800ms debounce already does).
