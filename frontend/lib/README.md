# frontend/lib

The frontend's plain TypeScript: the same-origin API client and error helpers, one client module per
backend area, the download hand-over, the wire types, and the pure row rules the workbench applies.
Nothing here renders; hooks and components import these functions.

| File | What it is |
| --- | --- |
| `api.ts` | `apiFetch()` (prefixes `/api`, sends the session cookie, JSON by default), `ApiError`, `signedOut()` (redirect to `/login` and return the error to throw), `errorFromResponse()` (reads a string `detail` or `error`, else `"<path> failed (<status>)"`) |
| `errors.ts` | `humanizeError()` (the one mapping from an error to a sentence), `isOffline()`, `lacksServerMessage()`, `isDuplicateRecord()` (the 409 an admin may confirm past) |
| `auth-api.ts` | `login()` (form-encoded), `logout()`, `register()`, `forgotPassword()`, `resetPassword()` |
| `documents-api.ts` | `listDocuments(owner?)` (an admin's `owner` asks for that account's records), `listTeamQueue()` (records nobody has taken yet), `uploadDocument(file, target?)`, `stageReceivedFile(file)` / `unstageReceivedFile(token)` (one received file sent as it is added, and dropped again), `receivedFilePreviewUrl(token)` (its first page as a PNG) and `combineReceivedFiles(upload)` (the staged files in order with their kinds, and the patient's details; an admin's `owner` puts the record in that account), `downloadRawPdf(id)` / `rawPdfUrl(id)` (the record's PDF as uploaded, by a plain link; a record put together from received files has its billing sheet on top), `setPatientId()`, `deleteDocument()`, `startIdentification()` |
| `received-files.ts` | The files a record arrives in: `RECEIVED_KINDS` in the order a record is put together (mirrors the backend's `received_files.KINDS`), `RECEIVED_KIND_LABELS`, `guessKind()` (from the file name), `shownKind()` (the server's reading, the name only when an unsure reading said records, and whether to look), `isAcceptedFile()` / `ACCEPTED_FILES` (PDFs and scanned images), `inReceivedOrder()`, `RECORDS_FROM` / `RECORDS_FROM_LABELS` (who sent the records; mirrors `received_files.PARTIES`) |
| `patient-id.ts` | The patient ID in an upload's file name - the backend's `patient_ids.py` rule: `patientIdFromFilename()`, `agreedPatientId()`, `cleanPatientId()`, `otherRecordsLine()`, `PATIENT_ID_MAX_LENGTH` |
| `review-api.ts` | The workbench calls: record detail, status, duplicates, dedup start, cancel, resolve, rows, segment and summarize start, header extract and save, summaries; the `HeaderFields` and `DuplicateAction` types |
| `admin-api.ts` | The accounts list (`listAccounts()`); categories list, create and update; prompt get, put and delete; reprocess; `assignDocument()` (move a record to another reviewer); the `AdminCategory`, `PromptInfo` and `CategoryInput` types |
| `billing-sheet.ts` | The office's billing sheet: `VISIT_TYPES` / `SUPPLEMENTAL` / `LATE_MEDS` (mirror the backend's `billing_sheet`; `visitLabel()` shows LATE as Late Meds, `takesAppointment()`; who reviews the records is not asked, the sheet always says IN-HOUSE), `BillingDraft` / `EMPTY_BILLING` / `billingReady()` (the fields typed at upload), `BillingSheet` / `BillingSheetEdit` / `toEdit()` / `editValid()` (the sheet's dialog), `isPageCount()`, `isPageCountList()` (the deposition pages: several counts separated by commas), `shownCounts()`, `depoPending()` (the deposition pages would print blank) |
| `deadlines.ts` | When a record's report is due and how My documents shows it: `daysLeft()`, `daysLeftLabel()`, `usDate()`, `deadlineTone()` (yellow for an appointment, light blue for late meds), `deadlineNote()`, `deadlineSortKey()` (closest first, no deadline after, Completed last) |
| `bundle-api.ts` | `BundleConfig`, `DIAGNOSTIC_OPERATIVE`, `DEPOSITIONS`, `BUNDLES` (the only copy of the bundle taxonomy), `downloadBundlePdf()`, `downloadBundleSummary()` |
| `download.ts` | `downloadFile()` (POST, then a native browser download of the prepared file), `fetchDownloadStatus()`, `PreparedDownload`, `DownloadState`, `DOWNLOAD_INTERRUPTED`, `DOWNLOAD_NOT_PREPARED` |
| `review-rows.ts` | Row keys (`withKeys()`, `newKey()`, `stripKeys()`), `sortRows()`, `mergeRows()`, `applyServerRowChanges()`, `SERVER_WRITABLE_FIELDS`, `touchKey()`, `touchedFields()`, `couldNotIdentify()`, `categoryWasGuessed()`, `rowErrors()`, `clearFlagOnEdit()`, `moveSharedBoundary()` |
| `summary-order.ts` | The Summaries tab's search and order: `findSummaries()`, `orderSummaries()`, `summaryDateValue()`, `SummaryOrder` |
| `types.ts` | Wire types: `CurrentUser`, `JobKind`, `JobState`, `JobProgress`, `DocumentStatus`, `DocumentListItem`, `DocumentDetail`, `Row`, `CategoryOption`, `SummaryItem`, `VerifyIssue`, duplicate types |
| `utils.ts` | `cn()`: `clsx` plus `tailwind-merge` |
| `api.test.ts`, `errors.test.ts`, `auth-api.test.ts`, `documents-api.test.ts`, `review-api.test.ts`, `admin-api.test.ts`, `bundle-api.test.ts`, `download.test.ts` | URL, method and body of each call; error mapping; the download hand-over and its failures |
| `review-rows.test.ts` | Every row rule, case by case |
| `patient-id.test.ts` | The file-name rule on the backend test's own cases, the agreed ID, the cleaning |
| `billing-sheet.test.ts` | The office's choices (Late Meds shown by its name), a page count, several deposition counts separated by commas, the upload's required billing fields (no appointment for SUPP or Late Meds), the sheet's edit and its checks, the counts the sheet prints and when it can be downloaded |
| `deadlines.test.ts` | Days left by calendar day, their label, the office's date, each row colour (SUPP, undated and Completed plain), the note under the date, the order |
| `received-files.test.ts` | The kind guessed from a file name, the server's reading over the name's and when to look, PDFs and images accepted and other files refused, the office's order by kind, the kinds and parties mirroring the server's |
| `summary-order.test.ts` | The search, date order and date reading of the Summaries tab |
| `review-rows.property.test.ts` | Invariants of the row rules over generated rows (`@fast-check/vitest`) |

## Use, run and test

Import with the alias, for example `import { apiFetch } from "@/lib/api"`. Tests stub `fetch` with
`vi.stubGlobal` and assert what was sent:

```bash
cd frontend
pnpm exec vitest run lib
```

## Documentation

- [Frontend routes and data reference](../../docs/reference/frontend-routes-and-data.md) (every call, wire types, error mapping)
- [The record workbench](../../docs/explanation/frontend-workbench.md) (row rules, downloads)
- [HTTP API reference](../../docs/reference/http-api.md)
