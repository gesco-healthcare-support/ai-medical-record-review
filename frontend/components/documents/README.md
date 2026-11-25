# frontend/components/documents

My documents, the signed-in landing page at `/`: upload a record (button, file picker or drag and
drop), combine several pre-split PDFs into one record, list the reviewer's records with filters,
search, sort and paging, and open, re-identify or delete them. An admin can also upload into another
reviewer's account and assign a record to someone.

| File | What it is |
| --- | --- |
| `documents-view.tsx` | `DocumentsView`: the hidden file input, whole-page drag and drop (first file only; none while the received-files or billing sheet dialog is open), the patient ID read from the file name or asked for (`PatientIdDialog`), upload toasts (with the patient's other records), the team queue above one's own records while records wait in it (`TeamQueue`: starting identification there takes the record, a record another reviewer has just taken says so), the empty state or the table, the received-files dialog, and the delete and re-identify confirmations. Upload does not start identification. For an admin, `OwnerPicker` ("Show records for") lists one other reviewer's records; while viewing them, delete is not offered, a note says changes are recorded under the admin, and an upload goes into that reviewer's account after a confirmation (`useUploads`, `UploadDialogs`), with a second confirmation when the server says the reviewer already has the file |
| `documents-table.tsx` | `DocumentsTable` (with `queue`, the team queue: no chips, search or Completed mark, who uploaded each record, "Take it and start identification", Delete for the uploader alone; the row menu is `RowMenu`): filter chips with counts, search on filename or patient name, sort on seven columns (default Due: the closest deadline first, `frontend/lib/deadlines.ts`), each row highlighted by its kind of deadline (yellow for an appointment, light blue for late meds), 20 per page, and a row menu with Open, Download raw PDF (the record as uploaded, `downloadRawPdf`), Start or Re-run identification (disabled while a job runs), Assign to... (admins only, disabled while a job runs) and Delete |
| `empty-state.tsx` | `EmptyState` and `UPLOAD_INPUT_ID`: the first-run dropzone (a `<label>` for the hidden input) and the three-step explainer |
| `received-files-dialog.tsx` | `ReceivedFilesDialog`: "Upload received files" - the patient's last and first name, the patient ID (filled when the files' names agree on one), the date received and who sent the records, all required, the billing sheet's fields (`billing-fields.tsx`; type of visit, the appointment date unless the visit is SUPP or Late Meds, and interpreter required), and the files the records arrived in (PDFs or scanned images), in any order, dragged onto the dialog anywhere (a cover while they are dragged; the drop stops at the dialog) or picked - the files come first, each on a card (green confirmed, amber not sure, red refused): each is sent on its own as it is added (`POST /api/documents/combine/files`), shows its pages or why it was refused (with Try again), takes the kind the model reads from its first page (the name only when an unsure reading said records), shows that first page on request, and is kept in the office's order (`frontend/lib/received-files.ts`); every file must be confirmed by the uploader (Looks right, or a kind picked), a file added twice is named and must be removed; a kind can be changed and a file moved; removing a file or closing the dialog deletes it on the server; sent to `POST /api/documents/combine` in the order listed once every file is read and confirmed; given `owner`, names that reviewer in its title and button and sends the record to their account |
| `dialog-layout.ts` | `DIALOG_FRAME` and `DIALOG_BODY`: a dialog taller than the window stays inside it and only its body scrolls, so the footer's buttons stay in view at any zoom; used by the received-files and billing sheet dialogs |
| `patient-id-dialog.tsx` | `PatientIdDialog`: asks for the patient ID before an upload whose file name carries none (or two); nothing is sent until one is typed |
| `billing-fields.tsx` | `BillingFields`: the billing sheet's fields - type of visit, the appointment date (gone for a supplemental report), doctor, interpreter, claim numbers, location, previous office visit and the pages the declaration states - shared by the received-files upload and the billing sheet dialog; who reviews the records is not asked (the sheet always says IN-HOUSE) |
| `billing-sheet-dialog.tsx` | `BillingSheetDialog`: the office's billing sheet of a record put together from received files (row menu "Billing sheet"): the fields typed at upload, editable, who sent the records, the office count and deposition pages worked out from the record (read again every few seconds while its pages are read), a count typed over one, the reason the counts differ (a note when the declared count and the office count differ), Save, and Download PDF whenever nothing is left unsaved (a deposition count not known yet prints blank) |
| `assign-dialog.tsx` | `AssignDialog`: an admin picks another reviewer and moves the record to them (`POST /api/admin/documents/{id}/assign`); when that reviewer already has the file it shows why and offers "Assign anyway" in the same dialog |
| `confirm-dialog.tsx` | `ConfirmDialog`: a Radix alert dialog for delete and re-identify |
| `status-pill.tsx` | `StatusPill`: the status label and tone for a record, with "(current/total)" while a job runs |
| `documents-table.test.tsx` | Sorting, the deadline order and row colours, filters (interrupted is not failed), paging when the list shrinks, search, row and menu clicks, the raw PDF download |
| `documents-view.test.tsx` | Upload errors, the patient ID (from the file name, asked for, cancelled, other records said), the drop target (nothing uploaded while the received-files dialog is open), the re-identify and delete confirmations, navigation, the admin's reviewer picker, uploading into another reviewer's account (both confirmations), and Assign offered to admins only |
| `billing-sheet-dialog.test.tsx` | The sheet's fields (the location among them) and worked-out counts shown, a location saved, the buttons outside the part that scrolls, the reading progress and the download held until both counts are known (or typed), the PDF downloaded, an edit saved (and the download held until it is), the server's answer taken as the saved state, a refused save, the note on a declared count that differs, whole-number counts, a doctor no longer on the list kept, loading, a record gone |
| `received-files-dialog.test.tsx` | Files dropped anywhere on the dialog added and the drop kept from the page under it, the cover while dragging, nothing taken while combining, the buttons outside the part that scrolls, the cards' colours, files put in the office's order by what their first page says whatever their names, the name used only when an unsure reading said records, every file confirmed before sending (even a sure reading), an unsure reading marked, a name disagreeing with a sure reading marked, a file added twice named and blocking, Try again, the first-page preview, a scanned image taken, the uploader's choice kept, a changed kind moving the file, moving and removing by hand (the file dropped on the server), each file sent on its own with its pages and the total, a refused or too-large file, a file removed while it is read, what is sent and in which order, the patient ID from the files' names, every detail (the billing sheet's required fields included) and every file read required, a whole-number declared page count, no appointment date for SUPP or Late Meds, the doctors offered and the optional billing fields sent, an admin's upload for a reviewer, a failed upload, non-PDF files reported, Cancel and Escape clearing everything and dropping the files on the server |
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
