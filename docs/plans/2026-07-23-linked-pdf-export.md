---
feature: linked-pdf-export
date: 2026-07-23
status: in-progress
base-branch: main
related-issues: []
---

## Goal

Add an "Export to linked PDF" option that produces one combined PDF -- the MRR summary letter
(two-column borderless: date | linked title + body) followed by the full uploaded source record
-- where each summary's title is a clickable link to that sub-document's first source page.

## Context & decisions

Why now: reviewers need to jump from a chronological summary straight to the (non-chronological)
source pages; today the only export is a Word `.docx` with no links. The langextract evaluation
concluded native tooling (`pymupdf`, already a dependency) does this better than a new library --
this feature is that native implementation. Reference artifact:
`Downloads\MRR Example - Andersen - Armstrong, Tonisha, 801992\*.ror-linked.pdf`.

Resolved Open Decisions:
- Decision: **native `pymupdf`** for the summary render because the head-to-head spike showed it
  reproduced the reference two-column proportions in 5 pages vs LibreOffice's 11, with robust
  deterministic link placement and no new dependency.
- Decision: **summary letter first, full source appended after** because that matches the
  reference PDF (the letter is the artifact; the source is the clickable exhibit).
- Decision: **two-column borderless format applies to BOTH exports** (Word + linked PDF) because
  the canonical `.ror-unlinked.doc` is two-column; the current inline Word layout is the outlier.
- Decision: **new endpoint `POST /{id}/export/pdf`** (parallel to `/export`) because the response
  media type differs; the existing Word route stays untouched.
- Decision: **single "Export" button -> dialog offering Word + linked PDF**, sharing the four
  header fields, because both exports need the same header inputs.
- Decision: **`[ManualCheck]` renders as a plain black prefix OUTSIDE the link** because the tag
  is transient (tester removes it per-doc, or it is dropped later) and must not be part of the
  clickable blue title.
- Decision: **new branch `feat/linked-pdf-export` off `main` (2664ed8)** because the feature is
  unrelated to this worktree's `feat/langextract-integration` spike.
- Decision (scope): **v1 covers the main record export only** -- NOT the bundle flows
  (`/diagnostics`, `/depositions`). Boilerplate letter wording is unchanged (reuse the existing
  intro); rewording to match the reference's exact prose is out of scope.

## All needed context

Backend (paths under `backend/`):
- Word export route to mirror: `app/api/documents.py:596` `export_document` (POST `/{id}/export`)
  -> `build_mrr_document` -> `StreamingResponse(DOCX_MIMETYPE)`.
- Export entry recomposition: `app/api/documents.py:533` `_export_entry(summary)` -> dict
  `{summaryDate, summaryTitle, summaryText}`; re-applies `(Pages a-b)`, `[Diagnostic Study]`,
  `[ManualCheck]` prefix, DOI. Word keeps this verbatim.
- Word doc builder to refactor: `app/services/reporting.py:47` `build_mrr_document(entries,
  num_pages, patient_name, patient_dob, qme_or_ame, lawfirm)`; helpers `_run` (line 22),
  `_add_inline_runs` (line 31) turn `**bold**`/`*italic*`/`_italic_` into runs; entries sorted
  chronologically by `summaryDate` (line 50 `safe_date_parse`). The per-entry paragraph loop is
  `reporting.py:118-123` -- this is what becomes a table.
- Summary data: `app/models.py:290` `Summary` -> `row_start`, `row_end`, `row_category`,
  `manual_check`, `excluded`, `effective_title()/effective_date()/effective_text()`.
- Included set: `app/api/documents.py:604` `[s for s in document.summaries if not s.excluded]`.
- Filename helper to mirror: `app/api/documents.py:562` `_summary_filename` (produces `.docx`).
- Source PDF path: `document.stored_path` (served at `documents.py:329`).
- PDF libs available (verified in `uv.lock`): `pymupdf>=1.24` (import as `pymupdf`), `pypdf`.
  PyMuPDF APIs used: `Story` + `DocumentWriter` + `Story.element_positions`, `page.get_text("dict")`
  span colors, `page.insert_link({"kind": pymupdf.LINK_GOTO, "page", "from", "to"})`,
  `Document.insert_pdf`, `page.insert_textbox`.
- Reporting tests: `backend/tests/test_reporting.py` (`test_build_mrr_document_saves`,
  `test_build_mrr_document_blank_qme_ame_does_not_crash`). Route tests:
  `backend/tests/test_documents_api.py:141` export-conflict test.

Frontend (paths under `frontend/`):
- Export button: `components/review/summaries-view.tsx:144-151` ("Export to Word") -> opens dialog;
  `ExportDialog` mounted at `summaries-view.tsx:317`.
- Dialog: `components/review/export-dialog.tsx` -- posts to `/api/documents/{id}/export`, downloads
  the blob via a temporary `<a>`; prefills the 4 header fields from `defaults` on open.
- Dialog test: `components/review/export-dialog.test.tsx`.

Known gotchas (from the spike, verified in the `mrr-lx` container):
- PyMuPDF `Story.element_positions` reports INLINE anchor rects as ZERO-WIDTH -- do NOT use them
  for hotspots (that was the "links don't work" bug). Locate the title's real rect another way.
- `pymupdf.Point(0, 0)` is the TOP-left of a page (PyMuPDF uses a top-left origin), so a GOTO
  `to=Point(0,0)` correctly lands at the top of the target page.
- Very large sources (a 380MB / 2600pp record) produce an equally large combined PDF; acceptable
  per the "always embed full source" decision. Known limitation, not a v1 blocker.

## Tasks (implementation blueprint)

### Task 0 -- Setup (build-time)
- what: create worktree/branch `feat/linked-pdf-export` off `main` (2664ed8). Arrange a
  verification stack (the warm `mrr-lx` stack currently runs on the langextract worktree -- do NOT
  disturb it without asking). Copy this plan into the new worktree's `docs/plans/`.
- approach: code
- acceptance: WHEN build begins, THE SYSTEM SHALL be on branch `feat/linked-pdf-export` based on
  `main`, with the plan present in `docs/plans/`.

### Task 1 -- MODIFY `backend/app/services/reporting.py`: two-column Word layout
- what: replace the per-entry paragraph loop (`reporting.py:118-123`) with a borderless 2-column
  table (`doc.add_table(rows=0, cols=2)`; the default "Table Normal" style has no borders). Per
  entry add a row: cell[0] width `Inches(0.9)` = `summaryDate`; cell[1] width `Inches(5.6)`,
  vertical-align top = `_add_inline_runs(p, summaryTitle, bold=True)` + `": "` + `_add_inline_runs(
  p, summaryText)`. Keep the header/title/intro/concludes paragraphs and chronological sort.
- pattern: existing `_run`/`_add_inline_runs` (`reporting.py:22,31`); table column widths set per
  cell (`cell.width = Inches(...)`), `table.autofit = False`.
- approach: test-after
- acceptance: WHEN `build_mrr_document` runs for N sorted entries, THE SYSTEM SHALL produce a doc
  containing exactly one table with 2 columns and N rows, cell[0]=date, cell[1]=title+": "+text,
  with no visible cell borders; AND `test_build_mrr_document_*` SHALL still pass.

### Task 2 -- CREATE `backend/app/services/linked_pdf.py`: `build_linked_pdf(...) -> bytes`
- what: `build_linked_pdf(source_path, entries, num_pages, patient_name, patient_dob, qme_or_ame,
  lawfirm) -> bytes`. `entries` = list of `{summaryDate, linkTitle, manualCheck, summaryText,
  startPage}` (chronologically sorted by caller). Steps:
  1. Build HTML (letter page, `<table>`: td.date width ~70px; td.body); each row body =
     optional `[ManualCheck] ` PLAIN + `<a class='ln'>{linkTitle}</a>. {inline-html body}`; CSS
     `a.ln{color:#0000EE;text-decoration:underline;font-weight:bold;}` and body font Times 11pt,
     justified. Reuse the letterhead (title=`qme_or_ame`, "MEDICAL RECORD REVIEW", the existing
     intro sentence with `num_pages`+`lawfirm`, "The following is a summary of those records:",
     trailing "This concludes...").
  2. Render via `Story`/`DocumentWriter` place/draw loop into an in-memory summary PDF.
  3. Draw a running `RE: <name> / DOB: <dob> / Page N` header on each summary page
     (`page.insert_textbox`, `fontname="tiro"`, 10pt; omit "Page 1").
  4. Merge: `combined = pymupdf.open(); combined.insert_pdf(summary); combined.insert_pdf(
     pymupdf.open(source_path))` (summary FIRST).
  5. Hotspots: for each summary page in order, collect `get_text("dict")` spans whose
     `color == 0x0000EE`; group maximal runs of consecutive blue spans (a black span ends a group)
     into per-title bounding rects (union); map groups in document order to `entries`; attach
     `insert_link({"kind": pymupdf.LINK_GOTO, "page": summary_pages + startPage - 1,
     "from": rect, "to": pymupdf.Point(0,0)})`.
  6. Return `combined.tobytes()`.
- pattern: the verified spike `scratchpad/spike_pdf_export_v2.py` (`build_native`), but replace its
  search-based hotspot with the blue-span detection above (multi-line safe, no search fragility).
- approach: test-after (extract the blue-span grouping as a pure helper `_title_hotspots(page)` and
  cover it `tdd`).
- acceptance:
  - WHEN `build_linked_pdf` runs with a P-page source and N entries, THE SYSTEM SHALL return a PDF
    of (summary_pages + P) pages with the summary letter before the source.
  - For each of the N entries, THE SYSTEM SHALL place exactly one `LINK_GOTO` with a hotspot of
    non-zero area over the entry's title, targeting page `summary_pages + startPage - 1`.
  - WHERE `manualCheck` is true, THE SYSTEM SHALL render `[ManualCheck] ` as plain text before the
    link and SHALL NOT include it inside the link hotspot (the blue span excludes it).

### Task 3 -- MODIFY `backend/app/api/documents.py`: `POST /{id}/export/pdf`
- what: add `_pdf_entry(summary)` (mirrors `_export_entry` but returns `linkTitle` = title WITHOUT
  the `[ManualCheck]` prefix, keeps `(Pages a-b)`/`[Diagnostic Study]`; plus `manualCheck` bool,
  `summaryDate`, `summaryText`, `startPage=summary.row_start`). Add `_linked_filename(document)`
  (mirror `_summary_filename`, suffix `_Medical_Records_linked.pdf`). Add route
  `export_document_pdf(payload: ExportPayload | None, document=Depends(get_owned_document), ...)`:
  included = non-excluded summaries (409 if none); build sorted entries; call
  `linked_pdf.build_linked_pdf(document.stored_path, entries, document.page_count, payload.patientName,
  payload.patientdob, payload.QMEorAME, payload.lawfirm)`; `audit(session, "export_pdf", user.id,
  document.id)`; `StreamingResponse(BytesIO, media_type="application/pdf", Content-Disposition
  attachment filename=_linked_filename(document))`.
- pattern: `export_document` (`documents.py:596`), `_export_entry` (533), `_summary_filename` (562).
- approach: test-after
- acceptance: WHEN `POST /{id}/export/pdf` is called with >=1 non-excluded summary, THE SYSTEM
  SHALL respond 200 `application/pdf` with a `.pdf` attachment; IF no non-excluded summary exists,
  THEN THE SYSTEM SHALL respond 409. WHERE the caller does not own the document, THE SYSTEM SHALL
  respond 404 (inherited from `get_owned_document`).

### Task 4 -- MODIFY `frontend/components/review/export-dialog.tsx`: two export actions
- what: retitle to "Export". Keep the 4 fields + prefill. Replace the single submit with two
  handlers sharing the field state: `submitWord()` -> POST `/export` (existing docx download),
  `submitPdf()` -> POST `/export/pdf` (download `application/pdf`, filename from
  `Content-Disposition`, fallback `linked.pdf`). Footer: two buttons "Export to Word" and
  "Export to linked PDF", both disabled while `busy`.
- pattern: existing `submit()` blob-download flow (`export-dialog.tsx:51-86`).
- approach: test-after
- acceptance: WHEN either footer button is clicked, THE SYSTEM SHALL POST the header fields to the
  matching endpoint and download the returned file; WHILE a request is in flight, THE SYSTEM SHALL
  disable both buttons.

### Task 5 -- MODIFY `frontend/components/review/summaries-view.tsx`: button label
- what: rename the button at `summaries-view.tsx:150` from "Export to Word" to "Export" (same
  disabled condition, same `setExportOpen(true)`).
- approach: test-after
- acceptance: WHEN summaries exist and >=1 is included, THE SYSTEM SHALL show an enabled "Export"
  button that opens the dialog.

### Task 6 -- MODIFY tests
- what: `test_reporting.py` add a two-column assertion (doc has one 2-col table with N rows) while
  keeping the two existing tests green. `test_documents_api.py` add: export/pdf 409 on no
  summaries; export/pdf 200 `application/pdf` on a doc with summaries (reuse the existing fixture
  pattern); assert the returned bytes open as a PDF whose page count == summary_pages + source
  pages and that contains N `LINK_GOTO` links with non-zero hotspots. `export-dialog.test.tsx`
  assert both buttons render and post to the correct endpoints.
- approach: test-after

## Validation loop

Backend (from `backend/`, inline env per the protect-secrets rule):
- `uv run ruff check . && uv run ruff format --check .`
- `uv run pytest tests/test_reporting.py tests/test_documents_api.py -q`
- Full suite: `uv run pytest -q`
- Link proof (the EARS acceptance, runnable): a pytest in `test_documents_api.py` builds a linked
  PDF from a small synthetic source + entries and asserts `page_count == summ + src`, `N` GOTO
  links, every hotspot `rect.width>1 and rect.height>1`, targets `== summ + startPage-1`.

Frontend (from `frontend/`):
- `pnpm typecheck`
- `pnpm test export-dialog summaries`
- Production build via Docker (host `pnpm build` fails on Windows): `docker compose build web`.

Live (on the agreed verification stack, warm doc):
- Export a linked PDF via the UI, open it, click a title -> jumps to that sub-document's first
  source page; confirm two borderless columns + running header. Verify programmatically as in the
  spike (readback GOTO links, non-zero hotspots, correct targets).

## Risk / rollback

- Blast radius: Task 1 changes the EXISTING Word export layout (inline -> two-column) for all
  users -- the only non-additive change. Everything else is additive (`pymupdf` already a dep; new
  route, new service, new dialog action). Rollback = revert `reporting.py` (restores inline Word)
  and drop the new route/service/dialog action.
- Risk: Story render edge cases (very long titles wrapping across a page break may split a blue-span
  group across two pages -> two hotspots for one title). Acceptable (both link to the same page);
  note in the builder. Risk: a summary with empty text is already prevented upstream
  (`EmptyExtractionError`), so entries always have body text.
- Risk: huge source -> huge combined PDF + memory during `insert_pdf`. Known limitation; a future
  streaming/size-guard is out of v1 scope.
