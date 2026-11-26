# frontend/components/documents - agent instructions

## Rules

- Upload must NOT start identification. A mis-picked file must not spend model quota; the reviewer
  starts it from the row menu or the workbench.
- Re-running identification replaces the rows and every correction. Confirm first whenever the record
  already has rows (`rows_count > 0`); start straight away otherwise.
- `interrupted` is not a failure. It has its own filter chip; do not fold it into "Failed". A restart
  or deploy produces it and the remedy is to run again.
- Status labels and tones live in `status-pill.tsx` (`STATUS_LABELS`, `STATUS_TONES`); filter chips in
  `documents-table.tsx` (`FILTERS`). A new document status needs an entry in both, and in
  `DocumentStatus` in `frontend/lib/types.ts`.
- Paging: show `curPage` (clamped to the current list), and step Prev/Next from it, not from the stored
  `page`. The list can shrink under the reader (a delete, a poll).
- Reject files the upload form cannot take with a message; never drop them silently. The form
  joins files into ONE record, so a silent drop is missing content.
- Dismissing the upload form (`received-files-dialog.tsx`), by any control, clears the staged
  files. Route every close through `close()`.
- "Upload records" is the ONE upload (the office's answer, 2026-11-26): every record is made through
  the upload form, so it has its dates and billing sheet. Do not add another upload path; the
  single-PDF `POST /api/documents` stays for scripts only.
- Errors go through `humanizeError()` with a record-specific `notFound` sentence.
- An upload into ANOTHER reviewer's account names the reviewer in the form's title and button. A
  file the office already has is warned about and asked about once before sending; nothing is
  refused. In the assign dialog, offer "anyway" only for `isDuplicateRecord(err)` - every other 409
  (a job running) is reported, never overridden.

## Traps

- `RECEIVED_KINDS` in `frontend/lib/received-files.ts` mirrors `KINDS` in
  `backend/app/services/received_files.py`; the server refuses any other kind. Change both.
- A drop on the page reaches the upload form through its `dropped` prop, added once per list (an
  effect guarded by the list's identity; strict mode runs effects twice). Pass a NEW array for
  every drop.
- The search placeholder says "filename"; the filter also matches the patient name.

## Commands

Run from `frontend/`:

```bash
pnpm exec vitest run components/documents hooks/use-documents lib/documents-api
pnpm typecheck
```

End to end, with the stack running on port 8080:

```bash
pnpm exec playwright test e2e/documents.spec.ts
```

## Docs

- `docs/reference/frontend-routes-and-data.md`
- `docs/reference/job-and-document-states.md`

<!-- reviewed: 2026-09-30 -->
