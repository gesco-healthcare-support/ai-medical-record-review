---
feature: Persisted, editable patient header (extract on identify)
date: 2026-07-21
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Auto-extract the patient's first name, last name, DOB, and law firm during identify, persist them on
the Document, and show them in an editable bar at the top of Review & correct with an explicit Save
(plus a manual re-extract) - unlocking items 2 (column) and 4 (filename).

## Context & decisions
- Backlog item 1 of 7 (MRR AI rewrite; W:\mrr-ai). Linchpin: items 2 + 4 depend on it.
- Today: extract_header(pdf, pages) -> {name, dob, lawfirm} exists (extraction.py) via POST
  /documents/{id}/extract-header, but it is on-demand only and NOT persisted; the result lives in
  transient React state used to prefill the Export dialog (review-page-client.tsx autoFill).
- Decisions (locked with Adrian 2026-07-21):
  - Extract FIRST + LAST name separately (+ keep a combined display name) because item 4's filename
    needs Lastname_Firstname; DOB + law firm as today.
  - Run automatically at the END of identify (segment_document) so it is ready on Review; KEEP a
    manual "Auto-fill" re-run button.
  - PERSIST on the Document (new columns + migration); editable bar at the top of Review with an
    EXPLICIT Save (not autosave).
  - Header extraction is best-effort in the worker: a failure logs + leaves the header blank, never
    fails identify (the rows are the primary output; the user can re-run via the button).
- API header keys are the persisted column names so the FE has ONE shape:
  patient_first_name, patient_last_name, patient_dob, law_firm. The extraction service stays
  domain-neutral ({first_name,last_name,dob,lawfirm}); the route/worker map to columns.

## All needed context
- extraction.py:23-60 _HEADER_SCHEMA/_BLANK/extract_header (reshape to first_name/last_name/dob/lawfirm).
- models.py:133 Document (+4 nullable columns) and listing() :159-170 (add header fields + a combined
  patient_name).
- alembic head = 009991f2eda1 (down_revision for the new migration).
- worker/tasks.py:85-103 segment_document.work() (persist header after the row loop; document is loaded).
- api/documents.py:239-247 get_document (uses listing -> gets header free); :218-236 list_documents
  (same); :250-258 extract_header_route (return mapped shape, still non-persisting); add PUT /{id}/header.
- schemas/documents.py (add HeaderPayload).
- FE: lib/types.ts DocumentListItem:35 + DocumentDetail:75 (+ header fields); lib/review-api.ts:43-50
  HeaderFields + extractHeader (reshape) + new saveHeader; hooks/use-review-workflow.ts boot():148 +
  watchSegment():111 fetch detail (surface header + setHeader); review-page-client.tsx autoFill/header
  state (replace with a HeaderBar); components/review/export-dialog.tsx defaults usage:42-47;
  summaries-view.tsx passes header through.

## Tasks
1. MODIFY extraction.py - schema/prompt/return {first_name,last_name,dob,lawfirm}; _BLANK likewise.
   approach: code. Acceptance: WHEN extract_header runs on text with a patient, THE SYSTEM SHALL return
   first_name and last_name as separate strings (each '' when unknown).
2. MODIFY models.py - Document gains patient_first_name/patient_last_name(String255), patient_dob
   (String32), law_firm(String512), all nullable; listing() adds those 4 + patient_name (first+last
   trimmed). approach: code. Acceptance: WHEN a Document is listed, THE SYSTEM SHALL include
   patient_first_name, patient_last_name, patient_dob, law_firm, and a combined patient_name.
3. CREATE alembic migration (down_revision 009991f2eda1) adding the 4 columns; downgrade drops them.
   approach: code. Acceptance: WHEN `alembic upgrade head` runs, THE SYSTEM SHALL add the 4 columns
   to documents.
4. MODIFY worker/tasks.py segment_document.work() - after rows, best-effort extract_header + set the
   4 Document fields; on PipelineError log (ids only) + continue. approach: code. Acceptance: WHEN
   identify completes on a record with a readable header, THE SYSTEM SHALL persist the extracted
   name/dob/law firm on the Document; IF extraction fails, THE SYSTEM SHALL still finish identify.
5. MODIFY schemas/documents.py - HeaderPayload(patient_first_name,patient_last_name,patient_dob,
   law_firm all default ""). MODIFY api/documents.py - extract_header_route returns the mapped shape
   (non-persisting); add PUT /{id}/header that writes the 4 fields + returns listing(). approach: code.
   Acceptance: WHEN a client PUTs /header, THE SYSTEM SHALL persist those fields; WHEN it POSTs
   /extract-header, THE SYSTEM SHALL return freshly extracted fields without persisting.
6. MODIFY lib/types.ts + lib/review-api.ts - add header fields to DocumentListItem/DocumentDetail;
   HeaderFields = {patient_first_name,patient_last_name,patient_dob,law_firm}; extractHeader ->
   HeaderFields; add saveHeader(id, fields) PUT. approach: code.
7. MODIFY hooks/use-review-workflow.ts - hold header state seeded from detail in boot() + watchSegment();
   expose header + setHeader. approach: code.
8. CREATE components/review/header-bar.tsx - editable bar (first, last, DOB, law firm) + Save (saveHeader
   -> onSaved) + Auto-fill (extractHeader -> populate, unsaved). MODIFY review-page-client.tsx - render
   HeaderBar atop the Review editor; drop the old "Auto-fill header" toolbar button + transient state;
   pass persisted header to SummariesView/ExportDialog. approach: test-after (UI). Acceptance: WHILE on
   Review with a record identified, THE SYSTEM SHALL show the editable header bar; WHEN the user edits a
   field and clicks Save, THE SYSTEM SHALL persist it; WHEN the user clicks Auto-fill, THE SYSTEM SHALL
   repopulate the fields from the record without saving.
9. MODIFY components/review/export-dialog.tsx + summaries-view.tsx - consume the new HeaderFields
   (patient = first + last; dob; law_firm). approach: code. Acceptance: WHEN the Export dialog opens with
   a persisted header, THE SYSTEM SHALL prefill patient name (first + last), DOB, and law firm.
10. MODIFY app/evaluators-ds.css - .rc-headerbar styles (inline fields + actions).

## Validation loop
- Backend: docker compose build api segment-worker && docker compose run --rm api alembic upgrade head
  (adds columns) - run at end-verification. Frontend: cd frontend && pnpm typecheck (clean).
- End verify (real browser): identify a record -> header bar populated; edit + Save persists across
  reload; Auto-fill repopulates; Export dialog prefilled.

## Risk / rollback
- Blast radius: Document schema (additive, nullable - safe), identify worker (one extra best-effort AI
  call), the review page header UI, export prefill. No change to existing rows/summaries.
- Rollback: alembic downgrade -1 (drops the 4 columns); git revert the commit.
