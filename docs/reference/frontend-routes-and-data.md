# Frontend routes and data reference

Every page route of the Next.js frontend, every backend call it makes, the query keys and polling
intervals it uses, how it turns errors into sentences, and the wire types and enumerations it reads.

Source of truth: `frontend/app/` (routes), `frontend/lib/` (API clients, types, row rules),
`frontend/hooks/` (queries, polling, the workbench lifecycle), `frontend/app/providers.tsx` (query
defaults).

## Routes

Every route is an App Router `page.tsx`. Pages are thin: each renders the app bar and one client
component, which does all data fetching in the browser. There is no `middleware.ts`, no Next API
route and no server-side data fetching.

| Route | File | Renders | Purpose |
| --- | --- | --- | --- |
| `/` | `frontend/app/page.tsx` `HomePage()` | `AppBar`, `DocumentsView` | My documents: upload, list, open, re-run identification, delete |
| `/login` | `frontend/app/login/page.tsx` `LoginPage()` | `LoginView` inside `Suspense` (required because it reads `useSearchParams`) | Sign in, register, request a reset link, set a new password |
| `/records/[id]` | `frontend/app/records/[id]/page.tsx` `RecordPage()` | `AppBar`, `ReviewPageClient` with `documentId` from the awaited params | The record workbench: Review & correct, Duplicates, Summaries, export |
| `/diagnostics` | `frontend/app/diagnostics/page.tsx` `DiagnosticsPage()` | `AppBar`, `BackLink`, `BundlePageClient` with `DIAGNOSTIC_OPERATIVE` | Diagnostic & Operative bundle builder |
| `/depositions` | `frontend/app/depositions/page.tsx` `DepositionsPage()` | `AppBar`, `BackLink`, `BundlePageClient` with `DEPOSITIONS` | Depositions bundle builder |
| `/admin` | `frontend/app/admin/page.tsx` `AdminPage()` | `AppBar`, `BackLink`, `AdminView` | Categories, summary prompts, reprocess a record |

The root layout (`frontend/app/layout.tsx` `RootLayout()`) wraps every route in `Providers` and a
bottom-centre `Toaster`, loads `globals.css` then `evaluators-ds.css`, and sets the title "MRR AI".

### Login views

`LoginView` (`frontend/components/auth/login-view.tsx`) picks its first view from the query string.
A signed-in user who reaches `/login` is sent to `/`.

| Address | View | Component |
| --- | --- | --- |
| `/login` | Sign in | `SignInForm` |
| `/login?view=register` | Create an account | `RegisterForm` |
| `/login?view=forgot` | Reset your password (request a link) | `ForgotForm` |
| `/login?view=reset` or `/login?token=<token>` | Set a new password; "Link expired" when there is no token | `ResetForm` |

### Navigation

| Control | Target | Shown |
| --- | --- | --- |
| Crest and wordmark in the app bar | `/` | Signed-in pages |
| Back link "My documents" | `/` | `/admin`, `/diagnostics`, `/depositions`, `/records/[id]` |
| User menu "Diagnostic & Operative" | `/diagnostics` | Always |
| User menu "Depositions" | `/depositions` | Always |
| User menu "Admin" | `/admin` | When `CurrentUser.is_superuser` is true |
| User menu "Sign out" | `POST /api/auth/logout`, clears the query cache, then `/login` | Always |
| Row click on My documents | `/records/<id>` | - |
| Bundle tabs | `/diagnostics` and `/depositions` | Bundle pages |
| "Fix categories in Review & correct" / "Open in Review & correct" | `/records/<id>` | Bundle pages, once a record is chosen |

Signed-out access: every page renders its shell, the first API call answers 401, and the client
redirects to `/login`. Behind the nginx proxy, paths under `/api/` and `/docs/` never reach the
frontend: the proxy sends them to the API and the documentation site (`deploy/nginx.conf`). Under
`pnpm dev`, `next.config.ts` rewrites `/api/*` to `API_ORIGIN`.

## Backend calls

All paths are under `/api` on the same origin. `apiFetch()` (`frontend/lib/api.ts`) adds the prefix,
sends the session cookie (`credentials: "include"`), sets `Accept: application/json`, and sets a JSON
`Content-Type` for any non-GET body that is not `FormData`. There is no CSRF header. Endpoint
behaviour is documented in the [HTTP API reference](http-api.md).

### Auth and user (`frontend/lib/auth-api.ts`, `frontend/hooks/use-auth.ts`, `frontend/hooks/use-current-user.ts`)

| Function | Method and path | Body | Response type | Used by |
| --- | --- | --- | --- | --- |
| `login()` | `POST /auth/login` | form-encoded `username` (the email), `password` | none | `useLogin()` in `SignInForm`, `RegisterForm` |
| `logout()` | `POST /auth/logout` | none | none | not called; `UserMenu` calls `apiFetch("/auth/logout")` directly and ignores its errors |
| `register()` | `POST /auth/register` | `{name, email, password}` | `CurrentUser` | `useRegister()` in `RegisterForm`, followed by a login |
| `forgotPassword()` | `POST /auth/forgot-password` | `{email}` | none | `useForgotPassword()` in `ForgotForm` |
| `resetPassword()` | `POST /auth/reset-password` | `{token, password}` | none | `useResetPassword()` in `ResetForm` |
| query in `useCurrentUser()` | `GET /users/me` | - | `CurrentUser` | `UserMenu`, `LoginView`, `AdminView` |

### Documents (`frontend/lib/documents-api.ts`, `frontend/hooks/use-documents.ts`)

| Function | Method and path | Body | Response type | Used by |
| --- | --- | --- | --- | --- |
| `listDocuments(owner?)` | `GET /documents`, or `GET /documents?owner=<id>` | - | `DocumentListItem[]` (the caller's own records, newest first; for an admin with `owner`, that account's) | `useDocuments()` in `DocumentsView`, `BundlePageClient`, `AdminView`; `useDocuments(owner)` in `DocumentsView` for an admin who picked a reviewer |
| `uploadDocument()` | `POST /documents` | multipart field `pdf`; `patient_id`; `owner` and `allow_duplicate` when an admin uploads into another reviewer's account | `{id, page_count, sha256_duplicate, patient_id, patient_records}` | `useUploadDocument()` in `DocumentsView`; does not start identification. The patient ID is read from the file name (`patientIdFromFilename` in `frontend/lib/patient-id.ts`, the backend's rule); a name with none, or two, opens `PatientIdDialog`, and nothing is sent until the uploader types one. The success message says how many other records the patient has. Into another reviewer's account it runs only after a confirmation, and a 409 with code `duplicate` asks a second time before sending `allow_duplicate` |
| `stageReceivedFile()` | `POST /documents/combine/files` | multipart field `pdf` (one PDF or scanned image) | `{token, pages, kind, check, sha256}` | `ReceivedFilesDialog`, once per file as it is added, one file at a time |
| `receivedFilePreviewUrl()` | `GET /documents/combine/files/{token}/preview` | - | a PNG of the file's first page, shown by a plain image tag | "Page 1" in `ReceivedFilesDialog` |
| `unstageReceivedFile()` | `DELETE /documents/combine/files/{token}` | - | `{ok}` | `ReceivedFilesDialog`: a file removed from the list, and every staged file when the dialog closes without making a record (errors ignored) |
| `combineReceivedFiles()` | `POST /documents/combine` | multipart `last_name`, `first_name`, `patient_id`, `date_received`, `records_from`, the billing sheet's `visit_type`, `interpreter`, `appointment_date` (not for `SUPP`) and, when typed, `doctor`, `claim_numbers`, `location`, `previous_visit`, `declared_pages`; `owner` when an admin opened the dialog on another reviewer's records, then one `tokens` and one `kinds` field per file in the order listed | `{id, page_count, parts, patient_id, patient_records}` | `useCombineReceivedFiles()` in `ReceivedFilesDialog`; does not start identification. The patient's details and the billing sheet's required fields (`billingReady` in `frontend/lib/billing-sheet.ts`) must be filled and every file read; the Patient ID box is filled from the ID the files' names agree on |
| `listDoctors()` | `GET /documents/combine/doctors` | - | `{doctors}` | `useDoctors()` in `ReceivedFilesDialog` (the Doctor list), fetched once while the dialog is open |
| `getBillingSheet()` | `GET /documents/{id}/billing` | - | the billing sheet | `useBillingSheet()` in `BillingSheetDialog`; read again every 5 seconds while `pages_unread` is above 0 |
| `saveBillingSheet()` | `PUT /documents/{id}/billing` | `BillingSheetEdit` (every field as typed) | the billing sheet | `useSaveBillingSheet()` in `BillingSheetDialog` (the answer replaces the cached sheet) |
| `downloadBillingSheet()` | `GET /documents/{id}/billing-sheet` (`billingSheetUrl()`) | - | the one-page PDF as an attachment, saved by the browser's download manager from a plain link | "Download PDF" in `BillingSheetDialog`, offered whenever nothing is unsaved; a deposition count not known yet prints blank (`depoPending` in `frontend/lib/billing-sheet.ts`) |
| `downloadRawPdf()` | `GET /documents/{id}/raw` (`rawPdfUrl()`) | - | the record's PDF as an attachment - for a record put together from received files, with its billing sheet as page 1 - saved by the browser's download manager from a plain link | "Download raw PDF" in each record's row menu (`DocumentsTable`, through `DocumentsView`'s `downloadRaw`), handed to the browser at once; for a record with `received_on`, `getBillingSheet()` follows and a toast says when the sheet's deposition pages print blank (`The deposition pages on its billing sheet are blank: the record's pages are still being read. Type them on the billing sheet, or download again later.`) |
| `setCompleted()` | `PUT /documents/{id}/completed` | `{completed}` | Document listing | `useSetCompleted()` in `DocumentsView` (the list is patched at once, then read again) |
| `setPatientId()` | `PUT /documents/{id}/patient-id` | `{patient_id}` | Document listing plus `patient_records` | `PatientIdLine` on the workbench (saves on Enter or when the box loses focus) |
| `deleteDocument()` | `DELETE /documents/{id}` | - | `{ok}` | `useDeleteDocument()` in `DocumentsView` |
| `startIdentification()` | `POST /documents/{id}/segment/start` | none | `{ok}` | `useStartIdentification()` in `DocumentsView` |

### Record workbench (`frontend/lib/review-api.ts`)

| Function | Method and path | Body | Response type | Used by |
| --- | --- | --- | --- | --- |
| `getDocument()` | `GET /documents/{id}` | - | `DocumentDetail` | `useReviewWorkflow()` (boot, after a segment run, `reloadRows()`); `BundlePageClient` query `["document", id]` |
| `getStatus()` | `GET /documents/{id}/status` | - | `{status, job, unreviewed_duplicate_groups?}` | `useReviewWorkflow()` `pollJob()` and `loadAttention()` |
| `getDuplicates()` | `GET /documents/{id}/duplicates` | - | `DuplicatesResponse` | `useDuplicates()` |
| `startDedup()` | `POST /documents/{id}/dedup/start` | `{fresh}` (default false) | `{ok}` | `useStartDedup()` ("Check duplicates", "Re-check duplicates"); `useReviewWorkflow()` `restartCancelled()` |
| `cancelJob()` | `POST /documents/{id}/jobs/{jobId}/cancel` | `{force}` | `JobProgress` plus `graceSeconds` | `useReviewWorkflow()` `cancelActiveJob()` |
| `resolveDuplicate()` | `POST /documents/{id}/duplicates/{group}/resolve` | `{action, primary_idx, idx}` (unused ones sent as null) | `{ok}` | `useResolveDuplicate()` in `DuplicatesView` |
| `saveRows()` | `PUT /documents/{id}/rows` | `{rows: Row[]}` | `{ok, count}` | `useReviewWorkflow()` autosave |
| `startSegment()` | `POST /documents/{id}/segment/start` | `{fresh}` (default false) | `{ok}` | `useReviewWorkflow()` `onStart()`, `restartCancelled()` |
| `startSummarize()` | `POST /documents/{id}/summarize/start` | `{rows, fresh, skip_duplicate_check}` | `{ok}` | `useReviewWorkflow()` `onSummarize()`, `restartCancelled()` |
| `extractHeader()` | `POST /documents/{id}/extract-header` | none | `DetectedHeaderFields`: `patient_first_name`, `patient_last_name`, `patient_dob`, `law_firm`, plus whichever of `attorney_name`, `doctor`, `letter_date`, `pages_received` the server's `HEADER_PREFILL_FIELDS` turns on (empty by default). `HeaderBar` merges the reply over the stored header rather than replacing it, so the fields not in the reply keep their values. | `HeaderBar`, `BundlePageClient` |
| `saveHeader()` | `PUT /documents/{id}/header` | `HeaderFields` | typed as `unknown` | `HeaderBar` |
| `getSummaries()` | `GET /documents/{id}/summaries` | - | `SummaryItem[]` (all of them; paged in the browser) | `useSummaries()` |
| `putSummary()` | `PUT /documents/{id}/summaries/{idx}` | any of `summaryTitle`, `summaryDate`, `summaryText`, `excluded`, `category` | `SummaryItem` | `useSaveSummary()` in `SummariesView` |
| `resummarize()` | `POST /documents/{id}/summaries/{idx}/resummarize` | none | `SummaryItem` | `useResummarize()` in `SummariesView` |

### Admin (`frontend/lib/admin-api.ts`, `frontend/hooks/use-admin.ts`)

| Function | Method and path | Body | Response type | Used by |
| --- | --- | --- | --- | --- |
| `listAccounts()` | `GET /admin/users` | - | `AdminAccount[]` (`{id, name, email}`, active accounts) | `useAccounts(enabled)` in `DocumentsView`, fetched only for an admin |
| `listCategories()` | `GET /admin/categories` | - | `AdminCategory[]` | `useCategories()` in `AdminView` |
| `createCategory()` | `POST /admin/categories` | `CategoryInput` plus `id` | `AdminCategory` | `useCreateCategory()` in `AdminView` via `CategoryDialog` |
| `updateCategory()` | `PATCH /admin/categories/{id}` | partial `CategoryInput` | `AdminCategory` | `useUpdateCategory()` (edit, activate, deactivate) |
| `getPrompt()` | `GET /admin/prompts/{id}` | - | `PromptInfo` | `PromptDialog` query `["admin", "prompt", id]`, enabled only while the dialog is open |
| `putPrompt()` | `PUT /admin/prompts/{id}` | `{text}` | `{category_id, text, custom}` | `useSavePrompt()` in `PromptDialog` |
| `deletePrompt()` | `DELETE /admin/prompts/{id}` | - | `PromptInfo` | `useRevertPrompt()` in `PromptDialog` |
| `reprocessDocument()` | `POST /admin/reprocess/{documentId}` | none | `{ok}` | `useReprocess()` in `AdminView`; the record list offered is the admin's own summarized records from `GET /documents` |
| `assignDocument()` | `POST /admin/documents/{documentId}/assign` | JSON `{owner, allow_duplicate}` | `{id, owner: {id, name}}` | `useAssignDocument()` (in `frontend/hooks/use-documents.ts`, so it refreshes the records list) in `AssignDialog`, opened from a record's "Assign to..." menu item on My documents for an admin |

### Files and downloads (`frontend/lib/download.ts`, `frontend/lib/bundle-api.ts`, `frontend/hooks/use-download-watch.ts`)

| Function | Method and path | Body | Response type | Used by |
| --- | --- | --- | --- | --- |
| `downloadFile()` | `POST <path>` | JSON | `PreparedDownload` `{token, url, filename?, size?}`; then clicks a detached anchor on `url` with `download` set | `ExportDialog`, `downloadBundlePdf()`, `downloadBundleSummary()` |
| `ExportDialog` via `downloadFile()` | `POST /documents/{id}/export` | `{patientName, patientdob, QMEorAME, lawfirm, includePageNumbers}` | `PreparedDownload` | Export to Word |
| `ExportDialog` via `downloadFile()` | `POST /documents/{id}/export/memo` | same as above | `PreparedDownload` | Download memo |
| `ExportDialog` via `downloadFile()` | `POST /documents/{id}/export/pdf` | same as above | `PreparedDownload` | Export to linked PDF |
| `ExportDialog` via `downloadFile()` | `POST /documents/{id}/export/zip` | same as above plus `bundles: [{label, categories, coverHeading, downloadName}]` from `BUNDLES` | `PreparedDownload` | Download all (.zip) |
| `downloadBundlePdf()` | `POST /documents/{id}/bundle/pdf` | `{categories, label, coverHeading, downloadName}` (`label` is the bundle slug) | `PreparedDownload` | `BundlePageClient` "Download combined PDF" |
| `downloadBundleSummary()` | `POST /documents/{id}/bundle/summarize` | `{categories, label, downloadName, patientName, patientdob, QMEorAME, lawfirm}` | `PreparedDownload` | `BundlePageClient` "Summarize to Word" |
| browser navigation | `GET /documents/{id}/downloads/{token}` (the `url` from `PreparedDownload`) | - | the file | the browser's download manager |
| `fetchDownloadStatus()` | `GET /documents/{id}/downloads/{token}/status` | - | `DownloadStatus` `{state, size}` | `useDownloadWatch()` in `ExportDialog`, `BundlePageClient` |
| pdf.js inside the iframe | `GET /documents/{id}/pdf` | - | the PDF | `PdfViewer` (loaded as `/pdfjs/web/viewer.html?file=...`) |

## Query keys and polling

TanStack Query holds all server state that is not the workbench's row buffer.

| Query key | Owner | Fetches | Refresh | Invalidated or patched by |
| --- | --- | --- | --- | --- |
| `["current-user"]` | `useCurrentUser()` | `GET /users/me` | No retry, 5 minute stale time | `useLogin()` success invalidates; sign out clears the whole cache |
| `["documents"]` | `useDocuments()` | `GET /documents` | Every 2000 ms while any record has an `active_job`, otherwise none | Upload, aggregate, assign, delete and start identification invalidate |
| `["documents", "owner", <id>]` | `useDocuments(owner)` | `GET /documents?owner=<id>` | As above | The same mutations: they invalidate the `["documents"]` prefix |
| `["admin", "users"]` | `useAccounts(enabled)` | `GET /admin/users` | None | None |
| `["duplicates", id]` | `useDuplicates()` (`duplicatesKey()`) | `GET /documents/{id}/duplicates` | Every 2000 ms while the latest dedup job is `queued` or `running`, otherwise none | Resolve and start dedup invalidate |
| `["summaries", id]` | `useSummaries()` (`summariesKey()`) | `GET /documents/{id}/summaries` | None | Save and re-draft patch the item in place; every settled summarize run invalidates |
| `["admin", "categories"]` | `useCategories()` | `GET /admin/categories` | None | Create, update, prompt save and prompt revert invalidate |
| `["admin", "prompt", id]` | `PromptDialog` | `GET /admin/prompts/{id}` | None; enabled only while the dialog is open | Prompt save and revert invalidate |
| `["document", id]` | `BundlePageClient` | `GET /documents/{id}` | None; enabled once a record is chosen | Nothing |

Timed loops outside TanStack Query:

| Loop | Interval | Runs while | Code |
| --- | --- | --- | --- |
| Workbench job poll | 1000 ms (`setTimeout` chain) | A segment or summarize run is watched | `frontend/hooks/use-review-workflow.ts` `pollJob()` |
| Autosave debounce | 800 ms after the last edit | Rows are dirty | `frontend/hooks/use-review-workflow.ts` `onRowsChange()` |
| Download watch | 2000 ms (`POLL_MS`), first check 2000 ms after the hand-over | Until `complete`, `expired`, a 404, 15 minutes (`WATCH_LIMIT_MS`), or the caller passes null or unmounts | `frontend/hooks/use-download-watch.ts` `useDownloadWatch()` |
| "Not started" threshold | 30000 ms (`NOT_STARTED_AFTER_MS`) of `waiting` | During a download watch | `frontend/hooks/use-download-watch.ts` |
| PDF viewer sync | 1000 ms (`setInterval`) | For the life of each `PdfViewer` | `frontend/components/review/pdf-viewer.tsx` |
| Stop escalation | `graceSeconds` from the cancel answer | After the first Stop press | `frontend/components/review/review-page-client.tsx` `onStop()` |

### Query client defaults

Set once in `frontend/app/providers.tsx` `Providers()`.

| Setting | Value |
| --- | --- |
| `staleTime` | 30000 ms |
| `refetchOnWindowFocus` | false |
| `retry` | One retry, except no retry for an `ApiError` with status 401, 403 or 404 |
| Query and mutation `onError` | A 401 sends the browser to `/login` unless it is already on a `/login` path |
| Tooltip provider delay | 200 ms |

## Client error handling

`apiFetch()` throws `ApiError(message, status)` for every failure.

| Server answer | Thrown |
| --- | --- |
| No response (offline, DNS, reset) | `ApiError("network", 0)` |
| 401 | `signedOut()`: redirects to `/login` unless already on a `/login` path, then throws `ApiError("signed out", 401)` |
| Other non-OK status | `errorFromResponse()`: the body's `detail` if it is a string, else its `error` if it is a string, else `"<path> failed (<status>)"` |
| 204 | returns `null` |

`humanizeError(err, {notFound?, fallback?})` (`frontend/lib/errors.ts`) turns any thrown value into the
sentence the UI shows:

| Input | Sentence |
| --- | --- |
| Not an `ApiError` | `fallback`, else the generic sentence |
| Status 0 | Couldn't reach the server. Check your connection and try again. |
| 401 | Your session has ended. Please sign in again. |
| 403 | You don't have permission to do that. |
| 404 | `notFound`, else "This item is no longer available - it may have been deleted or moved. Refresh and try again." |
| Message of the form `... failed (NNN)` (no server sentence) | `fallback`, else the generic sentence |
| Anything else | The server's own message |

The generic sentence is "Something went wrong on our end. Please try again; if it keeps failing,
contact your administrator."

| Helper | Returns true when |
| --- | --- |
| `isOffline(err)` | `err` is an `ApiError` with status 0; the auth forms use it and write their own copy for every other failure |
| `lacksServerMessage(err)` | The message is the synthesized `... failed (NNN)` form |

Download-specific sentences are exported from `frontend/lib/download.ts` (`DOWNLOAD_INTERRUPTED`,
`DOWNLOAD_NOT_PREPARED`) and `frontend/hooks/use-download-watch.ts` (`DOWNLOADING`, `NOT_STARTED`,
`EXPIRED`, `RECOVERED`, `COMPLETE`). Server-side messages are listed in the
[errors and messages reference](errors-and-messages.md).

## Browser storage

| Key | Store | Holds | Written by |
| --- | --- | --- | --- |
| `mrr.review.split` | `localStorage` | Left pane width in percent, Review & correct | `SplitPane` in `ReviewEditor` |
| `mrr.duplicates.split` | `localStorage` | Left pane width in percent, Duplicates | `SplitPane` in `DuplicatesView` |
| `mrr.summaries.split` | `localStorage` | Left pane width in percent, Summaries | `SplitPane` in `SummariesView` |

Nothing else is stored in the browser. The session is an HttpOnly cookie set by the backend.

## Wire types

Defined in `frontend/lib/types.ts` unless noted.

| Type | Fields |
| --- | --- |
| `CurrentUser` | `id`, `email`, `name` (nullable), `is_active`, `is_superuser`, `is_verified` |
| `JobProgress` | `id`, `kind`, `state`, `stage`, `current`, `total`, `error` (nullable), `attention?` |
| `JobAttention` | `message`, `rows: FailedRow[]` |
| `FailedRow` | `idx` (position among included rows), `pages` (`"start-end"`), `reason` |
| `DocumentListItem` | `id`, `original_filename`, `page_count`, `status`, `created_at`, `updated_at`, `active_job`, `rows_count`, `patient_first_name`, `patient_last_name`, `patient_name`, `patient_dob`, `law_firm`, `attorney_name?`, `doctor?`, `letter_type?`, `letter_date?`, `pages_received?` |
| `DocumentDetail` | The `DocumentListItem` fields except `rows_count`, plus `rows: Row[]`, `categories: CategoryOption[]`, `doctors?`, `letter_types?` |
| `Row` | `start`, `end`, `category`, `title`, `date`, `injury_date`, `flag`, `suggest_merge`, `include`, `ruled_paperwork?`, `method?`, `dupe_group?`, `dupe_primary?`, `dupe_dismissed?` |
| `EditorRow` (`frontend/lib/review-rows.ts`) | `Row` plus the client-only `_key`, stripped before sending |
| `CategoryOption` | `id`, `name` |
| `SummaryItem` | `idx`, `summaryTitle`, `summaryDate`, `summaryText`, `manualCheck`, `excluded`, `edited`, `verified`, `verifyChanged`, `verifyKeptRaw?`, `verifyFailed?`, `verifyIssues: VerifyIssue[]`, `originalText?` (nullable), `row: {start, end, category}`, `rowCategoryLive` (nullable), `rowMissing?`, `rowMethodLive?` |
| `VerifyIssue` | `type`, `detail` |
| `DuplicatesResponse` | `clusters: DuplicateCluster[]`, `job` (nullable), `stale`, `unreadable`, `checked` |
| `DuplicateCluster` | `group`, `dismissed`, `similarity` (0-1, nullable), `rows: DuplicateRow[]` |
| `DuplicateRow` | `idx`, `title`, `date`, `pages: {start, end}`, `include`, `primary` |
| `HeaderFields` (`frontend/lib/review-api.ts`) | `patient_first_name`, `patient_last_name`, `patient_dob`, `law_firm`, `attorney_name`, `doctor`, `letter_type`, `letter_date`, `pages_received` (all strings) |
| `AdminCategory` (`frontend/lib/admin-api.ts`) | `id`, `name`, `description`, `examples`, `active`, `auto_assign`, `summarize_default`, `has_summary_prompt` |
| `CategoryInput` (`frontend/lib/admin-api.ts`) | `name`, `description`, `examples`, `auto_assign`, `summarize_default`, `active` |
| `PromptInfo` (`frontend/lib/admin-api.ts`) | `category_id`, `text` (nullable), `effective_text`, `builtin_text`, `custom` |
| `BundleConfig` (`frontend/lib/bundle-api.ts`) | `label`, `slug`, `categories`, `coverHeading?`, `downloadName` |
| `PreparedDownload` (`frontend/lib/download.ts`) | `token`, `url`, `filename?`, `size?` |
| `DownloadStatus` (`frontend/lib/download.ts`) | `state`, `size` |

Optional fields marked "absent means nothing to say" (`verifyKeptRaw`, `verifyFailed`,
`originalText`, `rowMissing`, `rowMethodLive`) stay optional so an older backend during a rolling
deploy does not flag every card.

## Enumerations

| Enumeration | Values | Defined in |
| --- | --- | --- |
| `JobKind` | `segment`, `classify`, `summarize`, `dedup` | `frontend/lib/types.ts` |
| `JobState` | `queued`, `running`, `paused`, `done`, `needs_attention`, `error`, `interrupted`, `cancelled` | `frontend/lib/types.ts` |
| `DocumentStatus` | `uploaded`, `segmenting`, `summarizing`, `reviewing`, `done`, `needs_attention`, `error`, `interrupted` | `frontend/lib/types.ts` |
| `Row.method` | `rules`, `llm+embedding`, `llm-disagree`, `embedding-only`, `llm-only`, `no-signal`, `empty`, `timeout`; absent or null means unknown | `frontend/lib/types.ts` |
| `Row.flag` | `x` (flagged for review), `-` (not flagged) | `frontend/components/review/rows-table.tsx` |
| `DuplicateAction` | `keep_one`, `keep_another`, `unkeep`, `dismiss`, `remove_member` | `frontend/lib/review-api.ts` |
| `DownloadState` | `waiting`, `downloading`, `interrupted`, `complete`, `expired` | `frontend/lib/download.ts` |
| Letter type | `""` (Not set), `advocacy` (Advocacy letter), `interrogatory` (Interrogatory letter), `none` (No letter) | `frontend/components/review/header-bar.tsx` `LETTER_OPTIONS` |
| Workbench tab | `review`, `duplicates`, `summaries` | `frontend/components/review/review-page-client.tsx` |
| Workbench section | `loading`, `start`, `progress`, `editor`, `summaries` | `frontend/hooks/use-review-workflow.ts` `Section` |
| Save state | `""`, `saved`, `dirty`, `error` | `frontend/hooks/use-review-workflow.ts` `SaveState` |
| Auth view | `signin`, `register`, `forgot`, `reset` | `frontend/components/auth/login-view.tsx` |
| Bundle pane | `loading`, `failed`, `unidentified`, `ready` | `frontend/components/bundle/bundle-page-client.tsx` |

What the job and document states mean on the server is in the
[job and document states reference](job-and-document-states.md). The UI's labels for them:

| Document status | Status pill label | Tone |
| --- | --- | --- |
| `uploaded` | Uploaded | neutral |
| `segmenting` | Identifying documents | info |
| `reviewing` | Ready for review | warning |
| `summarizing` | Summarizing | info |
| `done` | Summarized | success |
| `needs_attention` | Needs attention | warning |
| `error` | Failed | danger |
| `interrupted` | Interrupted | danger |

While a job with a non-zero total is active, the pill appends "(current/total)"
(`frontend/components/documents/status-pill.tsx` `StatusPill()`).

My documents filter chips (`frontend/components/documents/documents-table.tsx` `FILTERS`), each with a
count:

| Chip | Matches |
| --- | --- |
| All | every record |
| Ready for review | status `reviewing` |
| Running | any record with an `active_job` |
| Summarized | status `done` |
| Uploaded | status `uploaded` |
| Failed | status `error` |
| Interrupted | status `interrupted` |
| Completed | the reviewer marked it Completed (`completed_at` set) |
| Not completed | not marked |

The Completed mark (a reviewer asked for it on 2026-11-05, like Outlook's "Categorize"): a bar
before each file name, outlined until the record is marked and filled blue after (`CompletedMark` in
`documents-table.tsx`). A click on it, or "Mark as completed" / "Clear Completed" in the row menu,
sets or clears it without opening the record. Under the status pill a record says when its review
was last downloaded (`downloaded_at`).

"Upload received files" (`ReceivedFilesDialog` in `received-files-dialog.tsx`) puts one record
together from the files it arrived in, the step the office did in Adobe Acrobat. The uploader types
the patient's last and first name, the patient ID (filled when the files' names agree on one), the
date received, picks who sent the records (Defense, Applicant, Insurance or Copy service), fills
the billing sheet's fields (`BillingFields` in `billing-fields.tsx`: the type of visit, the
appointment date - gone for a supplemental report, `SUPP` - and the interpreter are required;
who reviews the records is not asked, since the sheet always says IN-HOUSE; the doctor, the
claim numbers, the location, the previous office visit and the pages the declaration states may
stay blank), and adds the files
in any order - dragged onto the dialog anywhere (a cover says "Drop the files to add them" while
they are dragged; the drop never reaches the records page underneath, whose own drop handler would
upload the first file alone) or picked with Add files. The files come first in the dialog, so a
drop shows them at once and they are read while the rest is typed. Only the body scrolls: the
dialog stays inside the window and its Cancel and Combine & upload buttons stay in view at any zoom
(`dialog-layout.ts`, shared with the billing sheet dialog). Files may be PDFs, or scans sent as
images (JPG, PNG, TIFF), which the server turns into PDF pages; anything else (a Word or Outlook
file) is refused with a message saying to save it as PDF first (`isAcceptedFile` in
`frontend/lib/received-files.ts`). Each
file is sent on its own as soon as it is added (`stageReceivedFile`, three at a time -
`STAGE_AT_ONCE` - so the proxy's upload limit applies to each file rather than to the whole
record); the server counts its pages and the model reads its first page to say what it is,
giving up after 15 seconds (the keyword rules then guess and the file is marked for a look). Until it answers the file's row says
"Reading..."; then it shows the file's pages, or why the file was refused, with "Try again". A
refused file, or a file that is the same file as one above it ("Same file as ..."), must be removed
before the record can be sent. A file's kind is the server's reading; the name is used only when an
unsure reading said no more than medical records (`shownKind`). "Page 1" shows the file's first page,
and EVERY file must be confirmed - "Looks right", or a kind picked - before the record can be made,
because the model alone recognised the case's own papers only about 3 times in 4 (measured on the
live box, 2026-11-19). A reading the system is not sure of is marked "Not sure - check the
page" and its card is amber; a confirmed file's card is green, marked "Confirmed"; a refused or
repeated one is red. A kind the uploader confirmed is never overridden. The list keeps the files in
the office's order -
advocacy letter, declaration, medical index, medical records (depositions included), proof of
service, internal email, envelope - by kind, then in the order added. A kind can be changed (the
file moves to its place) and a file moved up or down by hand; the server joins them exactly as
listed. Removing a file, or closing the dialog without making a record, deletes the staged file on
the server. "Download raw PDF" in a record's row menu
saves the record's PDF as it was uploaded - for such a record, the joined file with its billing
sheet on top as page 1 - for the office's backup. It downloads at once; while the record's pages
are still being read the sheet's deposition pages print blank, and a toast says so.

"Billing sheet" in the row menu of such a record (one with `received_on`) opens `BillingSheetDialog`
(`billing-sheet-dialog.tsx`): the office's billing sheet - the fields typed at upload, editable,
who sent the records, the office count and the deposition pages worked out from the record, a count
typed over either (blank goes back to the worked-out one), and the reason the counts differ, with a
note when the declared page count and the office count differ. While the record's pages are still
being read it shows the office count, says the deposition pages are worked out once the pages
are read and print blank until then, and reads the sheet again every 5 seconds; "Download PDF"
works whenever nothing is left unsaved. Its body scrolls under a fixed
footer, like the upload's. While either dialog is open, a file dropped on the records page around
it uploads nothing.

Other My documents behaviour: search matches the filename or the patient name; sort columns are
Document, Patient, Pages, Uploaded (the default, newest first), Documents found and Last activity; 20
records per page.

For an admin viewing another reviewer's records ("Show records for"): both upload buttons stay,
and the record goes into that reviewer's account. A single upload first asks "Upload to <name>'s
records?"; if the server answers that the reviewer already has the file, a second confirmation
shows its sentence and offers "Upload anyway". The received-files dialog names the reviewer in its
title and button instead. Delete is not offered there. On any list an admin sees, each record's menu
has "Assign to...", which moves the record to another reviewer and asks again in the same dialog
when that reviewer already has the file.

Workbench progress labels (`STAGE_LABELS` in `frontend/hooks/use-review-workflow.ts`):

| Stage | Label |
| --- | --- |
| `starting` | Starting... |
| `reading` | Reading the pages |
| `segmenting` | Finding document boundaries |
| `categorizing` | Categorizing each document |
| `verifying` | Double-checking uncertain boundaries |
| `summarizing` | Writing summaries |
| `paused` | Paused - waiting for capacity, will retry automatically |

## Bundles

Defined once in `frontend/lib/bundle-api.ts` `BUNDLES`. The backend holds no copy: the bundle and zip
endpoints are handed these values.

| Constant | `label` | `slug` | `categories` | `coverHeading` | `downloadName` | Route |
| --- | --- | --- | --- | --- | --- | --- |
| `DIAGNOSTIC_OPERATIVE` | Diagnostic & Operative | `diagnostic-operative` | `3`, `8` | LIST OF DIAGNOSTIC AND OPERATIVE REPORTS | List of Diagnostic and Operative Reports | `/diagnostics` |
| `DEPOSITIONS` | Depositions | `depositions` | `9` | none (no cover page) | Depositions | `/depositions` |

A bundle page lists the rows whose category is in the set, leaving out a non-primary, non-dismissed
member of a duplicate cluster that has a primary, and, for a preset with `summarizedOnly` (both
presets), any row unticked for summary or marked `left_out_of_export` (its summaries all left out
on the Summaries tab). This mirrors `backend/app/services/bundles.py` `matched_rows()`. When every
matching row is unticked or left out, the page says so rather than that the documents are missing.
Changing a summary's "In export" box re-reads the record (`useSaveSummary` invalidates
`["document", id]`), so the list follows it.

## Constants

| Constant | Value | Code |
| --- | --- | --- |
| Default evaluation type | `PANEL QUALIFIED MEDICAL EVALUATION (ML-10*-)` | `DEFAULT_QME` in `frontend/components/review/export-dialog.tsx` and `frontend/components/bundle/bundle-page-client.tsx` |
| Records per page (My documents) | 20 | `PAGE_SIZE` in `frontend/components/documents/documents-table.tsx` |
| Summaries per page | 20 | `PAGE_SIZE` in `frontend/components/review/summaries-view.tsx` |
| Split pane | default 58%, range 24-70%, arrow-key step 2% | `frontend/components/review/split-pane.tsx` `SplitPane()` |
| Toast | 3500 ms, light theme | `frontend/components/ui/sonner.tsx` |
| Password rules (client mirror) | 8 or more characters, one number, one symbol | `frontend/components/auth/password-checklist.tsx` `passwordRules` |

Environment variables read by the frontend (`API_ORIGIN`, `E2E_BASE_URL`, `CI`) are listed in the
[configuration reference](configuration.md). No `NEXT_PUBLIC_*` variable exists.

## Related pages

- [The record workbench](../explanation/frontend-workbench.md)
- [How to extend the frontend](../how-to/extend-the-frontend.md)
- [HTTP API reference](http-api.md)
- [Design system reference](design-system.md)

<!-- reviewed: 2026-09-30 -->
