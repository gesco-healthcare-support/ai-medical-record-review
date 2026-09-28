# frontend/components/documents

My documents, the signed-in landing page at `/`: upload a record (button, file picker or drag and
drop), combine several pre-split PDFs into one record, list the reviewer's records with filters,
search, sort and paging, and open, re-identify or delete them.

| File | What it is |
| --- | --- |
| `documents-view.tsx` | `DocumentsView`: the hidden file input, whole-page drag and drop (first file only), upload toasts, the empty state or the table, the split-upload dialog, and the delete and re-identify confirmations. Upload does not start identification |
| `documents-table.tsx` | `DocumentsTable`: filter chips with counts, search on filename or patient name, sort on six columns (default newest upload first), 20 per page, and a row menu with Open, Start or Re-run identification (disabled while a job runs) and Delete |
| `empty-state.tsx` | `EmptyState` and `UPLOAD_INPUT_ID`: the first-run dropzone (a `<label>` for the hidden input) and the three-step explainer |
| `split-upload-dialog.tsx` | `SplitUploadDialog`: pick two or more PDFs and an optional record name, joined in the listed order, sent to `POST /api/documents/aggregate` |
| `confirm-dialog.tsx` | `ConfirmDialog`: a Radix alert dialog for delete and re-identify |
| `status-pill.tsx` | `StatusPill`: the status label and tone for a record, with "(current/total)" while a job runs |
| `documents-table.test.tsx` | Sorting, filters (interrupted is not failed), paging when the list shrinks, search, row and menu clicks |
| `documents-view.test.tsx` | Upload errors, the drop target, the re-identify and delete confirmations, navigation |
| `split-upload-dialog.test.tsx` | Non-PDF files reported, cancel clears the list, the name sent |
| `status-pill.test.tsx` | The progress suffix |

Data: `frontend/hooks/use-documents.ts` (`["documents"]`, polled every 2 s while any record has an
active job) and its upload, aggregate, delete and start-identification mutations.

## Use, run and test

```bash
cd frontend
pnpm exec vitest run components/documents
```

The end-to-end spec `frontend/e2e/documents.spec.ts` uploads a synthetic PDF, opens it and deletes it.

## Documentation

- [Frontend routes and data reference](../../../docs/reference/frontend-routes-and-data.md) (status labels, filters)
- [Job and document states reference](../../../docs/reference/job-and-document-states.md)
- [Design system reference](../../../docs/reference/design-system.md)
