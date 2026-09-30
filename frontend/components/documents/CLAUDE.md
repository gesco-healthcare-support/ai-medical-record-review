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
- Reject non-PDF files with a message; never drop them silently. The split dialog joins files into ONE
  record, so a silent drop is missing content.
- Dismissing the split dialog, by any control, clears the staged files. Route every close through
  `close()`.
- `UPLOAD_INPUT_ID` is declared in `empty-state.tsx` and imported by `documents-view.tsx`; the reverse
  import would be a cycle.
- Errors go through `humanizeError()` with a record-specific `notFound` sentence.

## Traps

- `isPdf()` is duplicated in `documents-view.tsx` and `split-upload-dialog.tsx`; change both.
- Drag and drop uploads only the first dropped file.
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
