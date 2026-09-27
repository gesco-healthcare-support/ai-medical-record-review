# Export formats reference

Every route that builds a deliverable, what it accepts, what it produces, what the file is called,
and every refusal it can give.

Source of truth: `backend/app/api/documents.py` (export and bundle routes, `_deliverable_filename()`,
`_download_name()`), `backend/app/schemas/documents.py` (payloads), `backend/app/services/reporting.py`,
`backend/app/services/linked_pdf.py`, `backend/app/services/bundles.py`,
`backend/app/services/downloads.py`, `backend/app/api/downloads.py`, `frontend/lib/bundle-api.ts`.

## Routes

All export routes are `POST`, take an optional JSON body, require a signed-in session, and act only
on a record the signed-in user owns. Each writes one audit event and answers with a prepared
download (see [Response](#response)), not with the file.

| Route | Output | Media type | Audit event | Needs included summaries |
| --- | --- | --- | --- | --- |
| `/api/documents/{document_id}/export` | MRR Word letter | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `export` | yes |
| `/api/documents/{document_id}/export/pdf` | Linked PDF: letter followed by the whole source record | `application/pdf` | `export_pdf` | yes |
| `/api/documents/{document_id}/export/memo` | Covering memo, Word | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `export_memo` | no |
| `/api/documents/{document_id}/export/zip` | ZIP archive of the letter, linked PDF, memo and bundle PDFs | `application/zip` | `export_zip` | yes |
| `/api/documents/{document_id}/bundle/pdf` | Combined PDF of category-matched sub-documents, optional cover page | `application/pdf` | `bundle_pdf` | no |
| `/api/documents/{document_id}/bundle/summarize` | Word letter summarizing only the category-matched sub-documents | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `bundle_summarize` | no |

"Included summaries" means summaries whose `excluded` flag is false.

## Request payloads

### `ExportPayload`

Body of `/export`, `/export/pdf` and `/export/memo`. An absent body is the same as every default.

| Field | Type | Default | Used by | Effect |
| --- | --- | --- | --- | --- |
| `patientName` | string | `""` | letter, linked PDF, memo | `RE:` header line; memo `RE: Review of <name>` |
| `patientdob` | string | `""` | letter, linked PDF | `DOB:` header line |
| `QMEorAME` | string | `""` | letter, linked PDF | Evaluation line under the header |
| `lawfirm` | string | `""` | letter, linked PDF, memo | Sender clause firm and summary-intro firm |
| `includePageNumbers` | boolean | `false` | letter, linked PDF | Appends `(Pages X-Y)` to each header line, from the summary's stored page range |

The export dialog (`frontend/components/review/export-dialog.tsx`) prefills the patient name, date
of birth and firm from the record's header and defaults the evaluation line to
`PANEL QUALIFIED MEDICAL EVALUATION (ML-10*-)`.

Values the routes take from the record or the session rather than the body:

| Value | Source | Used by |
| --- | --- | --- |
| Doctor | `documents.doctor` | Letter typeface; memo `TO:` line |
| Attorney name | `documents.attorney_name` | Sender clause |
| Covering-letter type and date | `documents.letter_type`, `documents.letter_date` | Covering-letter clause |
| Pages received | `documents.pages_received`, else `documents.page_count` | Opening paragraph count; accounting |
| Patient first and last name | `documents.patient_first_name`, `documents.patient_last_name` | Filenames |
| Reviewer name | Signed-in user's display name | Labor Code sentences; memo `FROM:` and signature |
| Page accounting | The record's review rows | Letter, linked PDF and memo closing sentences |

### `ExportZipPayload`

Body of `/export/zip`: every `ExportPayload` field plus:

| Field | Type | Default | Effect |
| --- | --- | --- | --- |
| `bundles` | list of `ZipBundle` | `[]` | One bundle PDF member per entry that matches at least one row |

`ZipBundle`:

| Field | Type | Default | Effect |
| --- | --- | --- | --- |
| `label` | string or null | `null` | Legacy filename slug when `downloadName` is absent |
| `categories` | list | `[]` | Categories to match, compared as strings; empty skips the bundle |
| `coverHeading` | string or null | `null` | Heading of a cover list page; absent or blank means no cover |
| `downloadName` | string or null | `null` | Filename suffix after the patient name |

### `BundlePayload`

Body of `/bundle/pdf` and `/bundle/summarize`.

| Field | Type | Default | Used by | Effect |
| --- | --- | --- | --- | --- |
| `categories` | list | `[]` | both | Categories to match, compared as strings; must be a non-empty list |
| `label` | string or null | `null` | both | Legacy filename slug when `downloadName` is absent |
| `coverHeading` | string or null | `null` | `/bundle/pdf` | Heading of a cover list page |
| `downloadName` | string or null | `null` | both | Filename suffix after the patient name |
| `model` | string or null | `null` | `/bundle/summarize` | Body model; default `Settings.model_for("body")` |
| `patientName` | string | `""` | `/bundle/summarize` | `RE:` header line |
| `patientdob` | string | `""` | `/bundle/summarize` | `DOB:` header line |
| `QMEorAME` | string | `""` | `/bundle/summarize` | Evaluation line |
| `lawfirm` | string | `""` | `/bundle/summarize` | Sender clause and summary-intro firm |

### Bundle presets sent by the frontend

Defined once in `frontend/lib/bundle-api.ts` and sent by both the bundle pages and the zip request.

| Preset | `label` | `categories` | `coverHeading` | `downloadName` | Page |
| --- | --- | --- | --- | --- | --- |
| Diagnostic & Operative | `diagnostic-operative` | `["3", "8"]` | `LIST OF DIAGNOSTIC AND OPERATIVE REPORTS` | `List of Diagnostic and Operative Reports` | `/diagnostics` |
| Depositions | `depositions` | `["9"]` | none | `Depositions` | `/depositions` |

## Response

Every export route answers `200` with JSON:

| Field | Type | Meaning |
| --- | --- | --- |
| `token` | string | 43 characters from `[A-Za-z0-9_-]` |
| `filename` | string | The name to save the file under |
| `size` | integer | Bytes |
| `url` | string | `/api/documents/<document_id>/downloads/<token>` |

The frontend (`frontend/lib/download.ts` `downloadFile()`) hands `url` to the browser as a download.

## Filenames

`_deliverable_filename()` builds `<Last>_<First>_Medical_Records[_<suffix>].<ext>` from the record's
stored patient last and first name (a blank one is skipped). With neither name stored it builds
`<original filename without extension>[_<suffix>].<ext>`. Every run of characters outside
`[A-Za-z0-9_.-]` becomes `_`, and leading or trailing `_` is removed; an empty result becomes the
fallback name.

| Deliverable | With a patient name | Without a patient name | Fallback |
| --- | --- | --- | --- |
| MRR Word letter | `<Last>_<First>_Medical_Records_summary.docx` | `<stem>_summary.docx` | `summaries.docx` |
| Linked PDF | `<Last>_<First>_Medical_Records_linked.pdf` | `<stem>_linked.pdf` | `record.pdf` |
| Memo | `<Last>_<First>_Medical_Records_memo.docx` | `<stem>_memo.docx` | `memo.docx` |
| Zip | `<Last>_<First>_Medical_Records.zip` | `<stem>.zip` | `record.zip` |
| Bundle PDF, `downloadName` given | `<Last>_<First>_Medical_Records_<downloadName>.pdf` | `<stem>_<downloadName>.pdf` | `records.pdf` |
| Bundle report, `downloadName` given | `<Last>_<First>_Medical_Records_<downloadName>.docx` | `<stem>_<downloadName>.docx` | `records.docx` |
| Bundle PDF or report, no `downloadName` | `<slug>.pdf` or `<slug>.docx` | same | `records.pdf` or `records.docx` |

- In `<downloadName>` spaces become `_` before sanitising.
- `<slug>` is `label` lower-cased with every run of characters outside `[a-z0-9]` replaced by `-` and
  leading or trailing `-` removed; a missing or empty label gives `records`.
- Zip members carry the same names as the single-file routes.
- If the JSON response has no filename, the frontend saves under its own fallback: `summaries.docx`,
  `record_linked.pdf`, `memo.docx`, `record.zip`, `<preset slug>.pdf`, `<preset slug>.docx`.

## Zip members

`ZIP_STORED` (no recompression), in this order:

| Order | Member | Condition |
| --- | --- | --- |
| 1 | MRR Word letter | Always (the route answers 409 without included summaries) |
| 2 | Linked PDF | Always |
| 3 | Memo | Always |
| 4 onward | One bundle PDF per `bundles` entry, in request order, with its cover page when `coverHeading` is set | The entry's `categories` is non-empty and matches at least one row; otherwise the entry is skipped |

The bundle report is never a member.

## Contents

| Deliverable | Contents |
| --- | --- |
| MRR Word letter | Page header (`RE:`, `DOB:`, `Page n` from page 2); evaluation line; `MEDICAL RECORD REVIEW`; opening paragraph; summary intro; one table row per delivered entry, sorted by date with undated last; page accounting; conclusion. Entries are the included summaries after provider-spelling consolidation and same-visit folding. Typeface from the record's doctor. |
| Linked PDF | The same letter rendered with PyMuPDF (running header on letter pages, header lines as links), then every page of the uploaded source PDF. Each header line links to its sub-document's first source page. No doctor typeface. |
| Memo | `TO:`, `FROM:`, `RE:`, `DATE:` lines (each only when known); `Greetings.`; opening sentence; page accounting; `Thank you.`; `Verified by:`; reviewer name; date (UTC, `Month DD, YYYY`). |
| Bundle PDF | Optional cover list page (`Date`, `PROVIDER`, `REPORT TITLE`), then the pages of every matched row in record order. Out-of-range pages are skipped. |
| Bundle report | The Word letter layout with only the law firm set: no doctor typeface, no covering-letter clause, no Labor Code sentences, no page accounting. One entry per matched row, summarized at request time with the row's category prompt and the audit off. A row whose pages all failed text recognition yields its notice entry. |

Row matching for both bundle routes (`bundles.py` `matched_rows()`): a row matches when its category
is in `categories`, unless it is a non-primary, non-dismissed member of a duplicate group that has a
primary. The row's `include` flag is not consulted. The cover page is omitted when every cell would
be empty.

Layout detail: [Deliverable layout](../explanation/deliverable-layout.md).

## Errors

HTTP errors raised by the routes carry `{"detail": "<message>"}`. Failures converted by
`_pipeline_error_response()` carry `{"error": "<message>"}`.

| Status | Body | Routes | When |
| --- | --- | --- | --- |
| 400 | `{"detail": "categories must be a non-empty list"}` | `/bundle/pdf`, `/bundle/summarize` | `categories` is missing, empty or not a list |
| 401 | `{"detail": "Not authenticated"}` | all | No session on a JSON request (a browser navigation is redirected to `/login` instead) |
| 404 | `{"detail": "not found"}` | all | The record does not exist or belongs to another user |
| 409 | `{"detail": "no summaries to export yet"}` | `/export`, `/export/pdf`, `/export/zip` | No included summaries |
| 409 | `{"detail": "no matching documents in this record"}` | `/bundle/pdf`, `/bundle/summarize` | No row matches after duplicate filtering |
| 409 | `{"detail": "<n> matching documents exceeds the on-demand limit of <cap>; use the main Summaries flow for a record this large"}` | `/bundle/summarize` | More matched rows than `BUNDLE_SUMMARIZE_CAP` |
| 422 | FastAPI validation error | all | The body does not match the payload schema |
| 422 | `{"error": "<EmptyExtractionError message>"}` | `/bundle/summarize` | Every matched row was skipped for having no readable text, or the causes were mixed |
| 422 | `{"error": "<TranscriptPagesUnreadableError message>"}` | `/bundle/summarize` | Every matched row was skipped because its transcript page numbers could not be read |
| 422 | `{"error": "<PdfUnreadableError message>"}` | `/bundle/summarize` | The source PDF cannot be opened |
| 503 | `{"detail": "Downloads are unavailable right now. Please try again."}` | all | Redis could not record the prepared download |
| 503 | `{"error": "<OcrUnavailableError message>"}` | `/bundle/summarize` | Text recognition is not available on the server |
| 500 | `{"error": "<message>"}` | `/bundle/summarize` | Any other `PipelineError` |
| 500 | Plain-text `Internal Server Error` | all | Any other exception, which the routes do not catch |

The message texts are listed in [Errors and messages](errors-and-messages.md).

## Download routes

The prepared file and its delivery status are fetched with two `GET` routes. Full route list:
[HTTP API](http-api.md).

| Route | Success | Refusals |
| --- | --- | --- |
| `GET /api/documents/{document_id}/downloads/{token}` | `200` with the file, or `206` for a `Range` request; audit event `download` | `404 {"detail": "not found"}` when the token is malformed, expired, made by another user or for another record, or its file is gone; `503` with the downloads-unavailable message when Redis cannot be reached |
| `GET /api/documents/{document_id}/downloads/{token}/status` | `200 {"state": <state>, "size": <bytes>}`, where the state is one of `waiting`, `downloading`, `interrupted`, `complete`, `expired`; no audit event | `404 {"detail": "not found"}` when the delivery record is gone or not this user's; `503` as above |

Headers on the file response:

| Header | Value |
| --- | --- |
| `Content-Disposition` | `attachment; filename="<filename>"` |
| `Cache-Control` | `no-store` |
| `X-Accel-Buffering` | `no` |
| `Content-Length` | The file size (set by `FileResponse`) |

## Settings

| Setting | Default | Named in `docker-compose.yml` | Effect |
| --- | --- | --- | --- |
| `DOWNLOAD_TTL_SECONDS` | 300 | no | Lifetime of the prepared file and of `download:<token>` |
| `DOWNLOAD_WATCH_SECONDS` | 900 | no | Lifetime of the delivery record `download:<token>:delivery` |
| `BUNDLE_SUMMARIZE_CAP` | 40 | no | Most matched rows `/bundle/summarize` will accept |
| `UPLOAD_FOLDER` | `./uploads` | yes, fixed to `/app/uploads` | Root of `<upload folder>/<user id>/downloads/` |

Every setting: [Configuration](configuration.md).

## Related pages

- [Exports and downloads](../explanation/exports-and-downloads.md)
- [Deliverable layout](../explanation/deliverable-layout.md)
- [How to add an API route or export](../how-to/add-an-api-route-or-export.md)
- [HTTP API](http-api.md)
