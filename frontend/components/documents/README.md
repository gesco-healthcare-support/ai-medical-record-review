# frontend/components/documents

My documents, the signed-in landing page at `/`: upload a record (button, file picker or drag and
drop), combine several pre-split PDFs into one record, list the reviewer's records with filters,
search, sort and paging, and open, re-identify or delete them. An admin can also upload into another
reviewer's account and assign a record to someone.

| File | What it is |
| --- | --- |
| `documents-view.tsx` | `DocumentsView`: the hidden file input, whole-page drag and drop (first file only), the patient ID read from the file name or asked for (`PatientIdDialog`), upload toasts (with the patient's other records), the empty state or the table, the received-files dialog, and the delete and re-identify confirmations. Upload does not start identification. For an admin, `OwnerPicker` ("Show records for") lists one other reviewer's records; while viewing them, delete is not offered, a note says changes are recorded under the admin, and an upload goes into that reviewer's account after a confirmation (`useUploads`, `UploadDialogs`), with a second confirmation when the server says the reviewer already has the file |
| `documents-table.tsx` | `DocumentsTable`: filter chips with counts, search on filename or patient name, sort on six columns (default newest upload first), 20 per page, and a row menu with Open, Download raw PDF (the record as uploaded, `downloadRawPdf`), Start or Re-run identification (disabled while a job runs), Assign to... (admins only, disabled while a job runs) and Delete |
| `empty-state.tsx` | `EmptyState` and `UPLOAD_INPUT_ID`: the first-run dropzone (a `<label>` for the hidden input) and the three-step explainer |
| `received-files-dialog.tsx` | `ReceivedFilesDialog`: "Upload received files" - the patient's last and first name, the patient ID (filled when the files' names agree on one), the date received, the appointment date and who sent the records, all required, and the files the records arrived in, each with a kind guessed from its name and kept in the office's order (`frontend/lib/received-files.ts`); a kind can be changed and a file moved; sent to `POST /api/documents/combine` in the order listed; given `owner`, names that reviewer in its title and button and sends the record to their account |
| `patient-id-dialog.tsx` | `PatientIdDialog`: asks for the patient ID before an upload whose file name carries none (or two); nothing is sent until one is typed |
| `assign-dialog.tsx` | `AssignDialog`: an admin picks another reviewer and moves the record to them (`POST /api/admin/documents/{id}/assign`); when that reviewer already has the file it shows why and offers "Assign anyway" in the same dialog |
| `confirm-dialog.tsx` | `ConfirmDialog`: a Radix alert dialog for delete and re-identify |
| `status-pill.tsx` | `StatusPill`: the status label and tone for a record, with "(current/total)" while a job runs |
| `documents-table.test.tsx` | Sorting, filters (interrupted is not failed), paging when the list shrinks, search, row and menu clicks, the raw PDF download |
| `documents-view.test.tsx` | Upload errors, the patient ID (from the file name, asked for, cancelled, other records said), the drop target, the re-identify and delete confirmations, navigation, the admin's reviewer picker, uploading into another reviewer's account (both confirmations), and Assign offered to admins only |
| `received-files-dialog.test.tsx` | The office's order by kind, a changed kind moving the file, moving and removing by hand, what is sent and in which order, the patient ID from the files' names, every detail required, an admin's upload for a reviewer, a failed upload, non-PDF files reported, Cancel and Escape clearing everything |
| `assign-dialog.test.tsx` | The reviewers offered, the move, the duplicate warning and "Assign anyway", the warning cleared on a new choice, other refusals |
| `status-pill.test.tsx` | The progress suffix |

Data: `frontend/hooks/use-documents.ts` (`["documents"]`, polled every 2 s while any record has an
active job) and its upload, received-files, assign, delete and start-identification mutations.

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
