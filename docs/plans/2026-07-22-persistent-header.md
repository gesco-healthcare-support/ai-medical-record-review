---
feature: Persist the auto-detected report header so it is detected once and available everywhere
date: 2026-07-22
status: draft
base-branch: main
related-issues: []
---

## Goal

Auto-detecting the patient name / DOB / law firm persists it to the document on the FIRST detect,
so it is available and editable on Review & correct, on Summaries, and prefilled into Export to Word
(and the bundle pages) without ever re-detecting - until the document is deleted.

## Context & decisions

Why now: today "Auto-fill" extracts via Vertex into LOCAL fields only; the user must also click Save
(`PUT /header`) or the data is lost, and the Summaries page never surfaces the header. So users
re-detect repeatedly. Also folds in the 3 SonarCloud smells from PR #32 (all in review-page-client).

Resolved decisions (modal 2026-07-22):
- Decision: **detect persists immediately** because "detect once, always available" - the extract
  endpoint will save onto the document and return it, so a single Auto-fill (and the bundle Auto-fill)
  writes it everywhere; the button relabels to "Re-detect" once a header exists.
- Decision: **editable header on BOTH Review and Summaries** because the user wanted it present on
  both pages; they stay in sync via the shared persisted header (each seeds from `wf.header`).
- Decision: **bundle pages reuse the persisted document header** (prefill from + persist to it) so
  detection is once-per-record everywhere.
- Decision: **fold the 3 Sonar fixes** (nested ternary + role="status") into this PR since they live
  in review-page-client, which this change already edits.

Constraint: RUNTIME change -> server redeploy after merge. No schema change (header columns exist).

## All needed context

- `extract_header_route` ([documents.py:261-271](backend/app/api/documents.py)): POST
  `/documents/{id}/extract-header` -> `extract_header(stored_path, pages)` -> `_header_shape(data)`;
  does NOT persist and takes no session. `put_header` ([documents.py:274-286](backend/app/api/documents.py))
  is the persistence pattern (sets `document.patient_first_name/last_name/patient_dob/law_firm` +
  `session.commit()` + returns `document.listing()`). `_header_shape` ([documents.py:251-258]) maps
  the extractor's `{first_name,last_name,dob,lawfirm}` -> the FE header keys.
- `HeaderBar` ([header-bar.tsx](frontend/components/review/header-bar.tsx)): `autoFill` (56-68) =
  `extractHeader` -> `setFields(data)` + `setDirty(true)` (local only); `save` (42-54) = `saveHeader`
  -> `onSaved`. Auto-fill button label at :117. Re-seeds from the `header` prop when not dirty (33-35).
- `extractHeader`/`saveHeader` ([review-api.ts:54-64](frontend/lib/review-api.ts)) -> POST
  extract-header / PUT header.
- `wf.header` + `setHeader` ([use-review-workflow.ts](frontend/hooks/use-review-workflow.ts)): seeded
  from `detail` on boot + after segment; the single source of the persisted header.
- `ReviewPageClient` ([review-page-client.tsx:145,159](frontend/components/review/review-page-client.tsx)):
  renders `<HeaderBar header={wf.header} onSaved={wf.setHeader} />` (review) and
  `<SummariesView header={wf.header} />` (no onSaved yet).
- `SummariesView` ([summaries-view.tsx:34-44](frontend/components/review/summaries-view.tsx)): takes
  `header`, passes it to `ExportDialog` `defaults` (prefill); does NOT render an editable header.
- `BundlePageClient` ([bundle-page-client.tsx:78-93](frontend/components/bundle/bundle-page-client.tsx)):
  `autoFill` = `extractHeader` -> local `patient/dob/firm`; the detail query (`getDocument`, :46-50)
  already returns the persisted header fields (`detail.patient_first_name` etc.).
- Sonar issues (PR #32): review-page-client.tsx:120,122 (nested ternary in the button `title`);
  :151 (`role="status"` on the invalid-rows banner).

## Tasks (implementation blueprint)

### T1 - Backend: extract-header persists + returns the saved header  [approach: test-after]
- what: MODIFY `extract_header_route` ([documents.py:261](backend/app/api/documents.py)) - add
  `session: Session = Depends(get_db)`; after `data = extract_header(...)`, write the mapped values
  onto `document` (mirror `put_header`: `patient_first_name/last_name/patient_dob/law_firm`) and
  `session.commit()`, then return `_header_shape(data)`. On `PipelineError` keep the existing 503/422
  (nothing persisted).
- pattern: `put_header` (documents.py:274-286) for the persist; `_pipeline_error_response` for errors.
- acceptance (EARS): WHEN POST /extract-header succeeds, THE SYSTEM SHALL persist the extracted
  header on the document AND return it, so a later GET /documents/{id} reflects the saved values. IF
  extraction raises a PipelineError, THE SYSTEM SHALL NOT change the stored header.

### T2 - HeaderBar: Auto-fill persists in one action + relabel to Re-detect  [approach: test-after]
- what: MODIFY `autoFill` ([header-bar.tsx:56](frontend/components/review/header-bar.tsx)) - after
  `extractHeader` (now persisted by T1), call `onSaved(data)` (updates the shared `wf.header`
  immediately) and `setDirty(false)`; toast "Header detected and saved." Relabel the button (:117):
  when the header already has any value (`patient_first_name || last_name || patient_dob || law_firm`)
  show "Re-detect" else "Auto-fill". Keep Save for manual edits.
- pattern: existing autoFill + the `onSaved` prop.
- acceptance (EARS): WHEN the user clicks Auto-fill and it succeeds, THE SYSTEM SHALL persist + reflect
  the header without a separate Save. WHERE a header already exists, THE button SHALL read "Re-detect".

### T3 - Editable header on the Summaries page  [approach: test-after]
- what: MODIFY `SummariesView` ([summaries-view.tsx:34](frontend/components/review/summaries-view.tsx))
  - add an `onHeaderSaved?: (h: HeaderFields) => void` prop and render `<HeaderBar documentId={documentId}
  header={header ?? null} onSaved={(f) => onHeaderSaved?.(f)} />` at the top of the section. MODIFY
  `ReviewPageClient` (:159) to pass `onHeaderSaved={wf.setHeader}` to `SummariesView`.
- pattern: the review page's own `<HeaderBar header={wf.header} onSaved={wf.setHeader} />`.
- acceptance (EARS): WHILE on the Summaries tab, THE SYSTEM SHALL show the same editable header;
  saving or re-detecting it there SHALL update the shared header (Review + Export reflect it).

### T4 - Bundle pages reuse the persisted header  [approach: test-after]
- what: MODIFY `BundlePageClient` ([bundle-page-client.tsx](frontend/components/bundle/bundle-page-client.tsx))
  - when `detail` loads, prefill `patient`/`dob`/`firm` from the persisted header
  (`${detail.patient_first_name} ${detail.patient_last_name}`.trim(), `detail.patient_dob`,
  `detail.law_firm`) if the fields are still empty; `autoFill` calls `extractHeader` (now persists via
  T1) and repopulates the form. `qme` stays bundle-local.
- pattern: the existing `autoFill` + the `detail` useQuery result.
- acceptance (EARS): WHEN a bundle page loads a record that has a saved header, THE SYSTEM SHALL
  prefill patient/DOB/firm from it; WHEN Auto-fill is used there, THE SYSTEM SHALL persist to the same
  document header.

### T5 - Clear the 3 SonarCloud smells (review-page-client)  [approach: code]
- what: MODIFY `review-page-client.tsx` - replace the nested-ternary `title` (117-125) with a
  computed `const summarizeHint` built via `if/else` (no nested ternary); change the invalid-rows
  banner (:151) `role="status"` to `aria-live="polite"` (same polite live-region, not the flagged role).
- pattern: n/a (cleanup).
- acceptance (EARS): WHEN the SonarCloud scan reruns on the PR, THE SYSTEM SHALL report 0 of these 3
  issues and the new-code quality gate SHALL pass.

### T6 - Tests  [approach: test-after]
- what: EXTEND `header-bar.test.tsx` (Auto-fill persists via onSaved + toast; button reads "Re-detect"
  when a header exists); ADD a Summaries header render test (header bar present + onHeaderSaved wired);
  ADD a bundle prefill test (form seeded from the persisted header); ADD a backend test that
  POST /extract-header persists (documents API test, Vertex mocked).
- pattern: the PR #32 component error-path tests (mock hooks/api).
- acceptance (EARS): WHEN `pnpm test` + backend pytest run, THE new suites SHALL pass; each new
  assertion SHALL be proven to fail under a deliberate mutation.

## Validation loop
- `cd frontend && pnpm test && pnpm typecheck`.
- `cd backend && uv run ruff check . && uv run ruff format --check .` (extract-persist test runs in CI
  / against the dev DB).
- Run `pr-review-toolkit:pr-test-analyzer` on the diff.
- Open PR into main; watch all jobs green; confirm SonarCloud shows the 3 issues cleared + gate green.
- After merge: server redeploy (git pull + `docker compose build api web` + `up -d`) - runtime change.

## Risk / rollback
Blast radius: the header flow across HeaderBar + review-page-client + summaries-view + bundle-page-client
+ the extract-header route. No schema change (columns exist), no migration. Behavior change:
extract-header now PERSISTS (re-detect overwrites the stored header - expected for a "detect" action).
Rollback: revert the squash PR.
Watch-out: two editable header surfaces (Review + Summaries) - they sync through the shared persisted
header (each re-seeds from `wf.header` when not mid-edit), so confirm a save on one reflects on the other.
