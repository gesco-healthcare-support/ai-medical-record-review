# HTTP API reference

Every HTTP route the FastAPI backend serves - 47 routes in six routers - with its auth level,
request, response, status codes and audit action.

Source of truth: `backend/app/main.py`, `backend/app/api/documents.py`,
`backend/app/api/downloads.py`, `backend/app/api/admin.py`, `backend/app/auth/routes.py` (the
FastAPI-Users 15.0.5 routers it mounts), `backend/app/schemas/documents.py`,
`backend/app/schemas/admin.py`, `backend/app/auth/schemas.py`, and the `listing()`, `progress()`
and `as_row()` methods in `backend/app/models.py`.

## Conventions

| Item | Value |
| --- | --- |
| Base path | Every route except `/health` is under `/api/`. Document and download routes are under `/api/documents`. |
| Request content type | JSON, except `POST /api/auth/login` (form body, `application/x-www-form-urlencoded`) and the two upload routes (`multipart/form-data`). |
| Response content type | JSON, except `GET .../pdf` and `GET .../downloads/{token}` (files) and 204 responses (no body). |
| Envelope | None. A success body is a bare JSON object, array or `null`. |
| Session | The `mrr_session` cookie set by `POST /api/auth/login`. |
| Timestamps | UTC ISO 8601 strings with an offset (`_utc_iso` in `backend/app/models.py`). |
| Error bodies | See [Errors and messages reference](errors-and-messages.md). |

Auth levels used in the tables below:

| Level | Meaning | Refusal |
| --- | --- | --- |
| Public | Path is on the gate's allowlist (`_PUBLIC_EXACT`, `_PUBLIC_PREFIXES` in `backend/app/auth/deps.py`). No session needed. | - |
| Login | Any signed-in, active user (`enforce_auth` in `backend/app/auth/deps.py`, attached to the whole app in `backend/app/main.py`). | 401 `{"detail": "Not authenticated"}`; 302 to `/login` instead when the `Accept` header contains `text/html` and not `application/json`. |
| Owner | Login, and the `{document_id}` in the path belongs to the caller, or the caller is an admin (`get_owned_document` in `backend/app/api/deps.py`). | 404 `{"detail": "not found"}` - the same answer as an id that does not exist. |
| Admin | Login, and the user's `is_admin` column is true (FastAPI-Users calls it `is_superuser`). | 403 `{"detail": "Admin only"}` from the gate on `/api/admin/...`; 403 `{"detail": "Forbidden"}` from FastAPI-Users on `/api/users/{id}`. |

Every route that is not Public can also answer 401 (or 302). Every route with a typed path parameter,
a form body or a JSON body can also answer 422 (FastAPI request validation). The "Other statuses"
columns list only the statuses beyond those.

## Route count

| Router | Defined in | Prefix | Routes |
| --- | --- | --- | --- |
| health | `backend/app/main.py` `health()` | none | 1 |
| auth | `backend/app/auth/routes.py` `auth_router` | `/api/auth` | 5 |
| users | `backend/app/auth/routes.py` `users_router` | `/api/users` | 5 |
| documents | `backend/app/api/documents.py` `router` | `/api/documents` | 25 |
| downloads | `backend/app/api/downloads.py` `router` | `/api/documents` | 2 |
| admin | `backend/app/api/admin.py` `router` | `/api/admin` | 9 |
| Total | | | 47 |

FastAPI also generates `/docs`, `/docs/oauth2-redirect`, `/redoc` and `/openapi.json` on the API
process. They are not counted above and are public by prefix.

## Where the API is reachable

| Environment | What reaches FastAPI |
| --- | --- |
| Compose stack, through the `proxy` service (port 8080) | Only paths under `/api/` (`deploy/nginx.conf`). `/docs/` is the documentation site container; `/health`, `/redoc` and `/openapi.json` are answered by the Next.js `web` container. |
| Compose stack, inside the Docker network | `api:8000`. The `api` service publishes no host port (`docker-compose.yml`). |
| Local development | `uvicorn` on `127.0.0.1:8000`. The Next.js dev server rewrites `/api/:path*` to `API_ORIGIN`, default `http://127.0.0.1:8000` (`frontend/next.config.ts`). |

## Live OpenAPI schema

The generated schema is titled `MRR AI API`, version `0.1.0` (`backend/app/main.py`).

| Environment | How to read it |
| --- | --- |
| Local development | Browse `http://127.0.0.1:8000/docs` (Swagger UI) or `http://127.0.0.1:8000/redoc`. |
| Compose stack | Fetch it from inside the `api` container (command below). |

Local development, JSON to a file:

```bash
curl -s http://127.0.0.1:8000/openapi.json > openapi.json
```

Compose stack, run from the checkout that holds `docker-compose.yml`:

```bash
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/openapi.json').read().decode())" > openapi.json
```

## Health

| Method | Path | Auth | Request | Success | Other statuses | Audit |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/health` | Public | none | 200 `{"status": "ok"}` | none | none |

## Auth router

| Method | Path | Auth | Request | Success | Other statuses | Audit |
| --- | --- | --- | --- | --- | --- | --- |
| POST | `/api/auth/login` | Public | Form fields `username` (the email) and `password` | 204; `Set-Cookie: mrr_session=<token>` | 400 `LOGIN_BAD_CREDENTIALS` | none |
| POST | `/api/auth/logout` | Login | none | 204; cookie cleared (`Max-Age=0`) | none | none |
| POST | `/api/auth/register` | Public | JSON `UserCreate` | 201 `UserRead` | 400 `REGISTER_USER_ALREADY_EXISTS`; 400 `REGISTER_INVALID_PASSWORD` with `reason` | none |
| POST | `/api/auth/forgot-password` | Public | JSON `{"email": "<address>"}` | 202 `null` | none | none |
| POST | `/api/auth/reset-password` | Public | JSON `{"token": "<token>", "password": "<new password>"}` | 200 `null` | 400 `RESET_PASSWORD_BAD_TOKEN`; 400 `RESET_PASSWORD_INVALID_PASSWORD` with `reason` | none |

| Route | Behaviour |
| --- | --- |
| `POST /api/auth/login` | Looks the user up by email and verifies with `MrrPasswordHelper` (`backend/app/auth/password.py`). An unknown email, a wrong password and an inactive user all give the same 400. On success writes an `access_token` row and sets the cookie: `HttpOnly`, `SameSite=lax`, `Path=/`, `Max-Age=43200`, plus `Secure` when `ENVIRONMENT=prod` (`backend/app/auth/backend.py`). |
| `POST /api/auth/logout` | Deletes the caller's `access_token` row, then clears the cookie. |
| `POST /api/auth/register` | `name` is required. The password must pass `UserManager.validate_password` (8+ characters, a digit, a non-alphanumeric symbol). `is_active`, `is_superuser` and `is_verified` in the body are ignored, so a new account is an active non-admin. No verification step. |
| `POST /api/auth/forgot-password` | Answers 202 for every address. For an active user it creates a reset token and calls `UserManager.on_after_forgot_password`, which logs `password reset requested user_id=<id>` and nothing else: no email is sent (`backend/app/auth/users.py`). |
| `POST /api/auth/reset-password` | The token is a JWT signed with `SECRET_KEY`, valid for 3600 seconds (FastAPI-Users default), carrying a fingerprint of the password hash at the time it was issued. The new password must pass the same rule as registration. |

## Users router

| Method | Path | Auth | Request | Success | Other statuses | Audit |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/users/me` | Login | none | 200 `UserRead` | none | none |
| PATCH | `/api/users/me` | Login | JSON `UserUpdate` | 200 `UserRead` | 400 `UPDATE_USER_EMAIL_ALREADY_EXISTS`; 400 `UPDATE_USER_INVALID_PASSWORD` with `reason` | none |
| GET | `/api/users/{id}` | Admin | none | 200 `UserRead` | 403; 404 `{"detail": "Not Found"}` | none |
| PATCH | `/api/users/{id}` | Admin | JSON `UserUpdate` | 200 `UserRead` | 400 (as `PATCH /me`); 403; 404 | none |
| DELETE | `/api/users/{id}` | Admin | none | 204 | 403; 404 | none |

| Route | Behaviour |
| --- | --- |
| `PATCH /api/users/me` | Applies `email`, `password` and `name`. `is_active`, `is_superuser` and `is_verified` are ignored. A new password is validated and hashed like registration. |
| `GET`, `PATCH`, `DELETE /api/users/{id}` | `{id}` is the integer user id; a non-integer id is a 404. There is no route that lists users or looks one up by email. |
| `PATCH /api/users/{id}` | Applies every `UserUpdate` field, including `is_active` and `is_superuser` (the admin flag). |
| `DELETE /api/users/{id}` | Deletes the `user` row; the database cascades its `access_token` rows. `documents.user_id` and `audit_log.user_id` reference the user with no `ON DELETE` rule. |

The web app calls only `GET /api/users/me` from this router (`frontend/hooks/use-current-user.ts`).

## Documents router

Every path below starts with `/api/documents`. `{document_id}` is the document's UUID string.

| # | Method | Path | Auth | Request | Success | Other statuses | Audit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D1 | POST | `/api/documents` | Login | Multipart file field `pdf` | 201 `{id, page_count, sha256_duplicate}` | 400 | `upload` |
| D2 | POST | `/api/documents/aggregate` | Login | Multipart file field `pdfs` (repeated, one per file), optional form field `name` | 201 `{id, page_count, records}` | 400, 503 | `aggregate_upload` |
| D3 | GET | `/api/documents` | Login | query `owner` (int, optional; honoured for an admin only) | 200 array of Document listing plus `rows_count` | none | none |
| D4 | GET | `/api/documents/{document_id}` | Owner | none | 200 Document listing plus `rows`, `categories`, `doctors`, `letter_types`, `owner` | none | `view_record` when an admin opens another account's record |
| D5 | POST | `/api/documents/{document_id}/extract-header` | Owner | none | 200 `{patient_first_name, patient_last_name, patient_dob, law_firm}` | 422, 503, 500 `{"error"}` | `header.extract` |
| D6 | PUT | `/api/documents/{document_id}/header` | Owner | JSON `HeaderPayload` | 200 Document listing | none | `header.edit` |
| D7 | DELETE | `/api/documents/{document_id}` | Owner (not admin) | none | 200 `{"ok": true}` | 404, 409 | `delete` |
| D8 | GET | `/api/documents/{document_id}/pdf` | Owner | none | 200 `application/pdf`; 206 for a `Range` request | none | `view_pdf` |
| D9 | GET | `/api/documents/{document_id}/status` | Owner | none | 200 `{status, job, unreviewed_duplicate_groups}` | none | none |
| D10 | GET | `/api/documents/{document_id}/duplicates` | Owner | none | 200 `{clusters, job, stale, unreadable, checked}` | none | none |
| D11 | POST | `/api/documents/{document_id}/dedup/start` | Owner | Optional JSON `DedupStartPayload` | 200 `{"ok": true}` | 409, 503 | `dedup.start` |
| D12 | POST | `/api/documents/{document_id}/duplicates/{group}/resolve` | Owner | JSON `DuplicateResolvePayload` | 200 `{"ok": true}` | 400, 404, 409 | `duplicates.resolve` |
| D13 | PUT | `/api/documents/{document_id}/rows` | Owner | JSON `RowsPayload` | 200 `{ok, count, reopened}` | 400, 409 | `rows.edit` |
| D14 | POST | `/api/documents/{document_id}/jobs/{job_id}/cancel` | Owner | Optional JSON `CancelPayload` | 200 Job progress plus `graceSeconds` | 404 | `job.cancel` |
| D15 | POST | `/api/documents/{document_id}/segment/start` | Owner | Optional JSON `SegmentStartPayload` | 200 `{"ok": true}` | 409, 503 | `segment.start` |
| D16 | POST | `/api/documents/{document_id}/summarize/start` | Owner | Optional JSON `SummarizeStartPayload` | 200 `{"ok": true}` | 400, 409, 503 | `rows.edit`, `summarize.skip_duplicate_check` |
| D17 | GET | `/api/documents/{document_id}/summaries` | Owner | none | 200 array of Summary | none | none |
| D18 | PUT | `/api/documents/{document_id}/summaries/{idx}` | Owner | Optional JSON `SummaryEditPayload` | 200 Summary | 400, 404, 409 | `summary.category`, `summary.edit` |
| D19 | POST | `/api/documents/{document_id}/summaries/{idx}/resummarize` | Owner | Optional JSON `ResummarizePayload` | 200 Summary | 404, 409; 422, 503, 500 `{"error"}` | `resummarize` |
| D20 | POST | `/api/documents/{document_id}/export` | Owner | Optional JSON `ExportPayload` | 200 Prepared download (Word MRR) | 409, 503 | `export` |
| D21 | POST | `/api/documents/{document_id}/export/pdf` | Owner | Optional JSON `ExportPayload` | 200 Prepared download (linked PDF) | 409, 503 | `export_pdf` |
| D22 | POST | `/api/documents/{document_id}/export/memo` | Owner | Optional JSON `ExportPayload` | 200 Prepared download (memo) | 503 | `export_memo` |
| D23 | POST | `/api/documents/{document_id}/export/zip` | Owner | Optional JSON `ExportZipPayload` | 200 Prepared download (zip) | 409, 503 | `export_zip` |
| D24 | POST | `/api/documents/{document_id}/bundle/pdf` | Owner | Optional JSON `BundlePayload` | 200 Prepared download (bundle PDF) | 400, 409, 503 | `bundle_pdf` |
| D25 | POST | `/api/documents/{document_id}/bundle/summarize` | Owner | Optional JSON `BundlePayload` | 200 Prepared download (bundle Word report) | 400, 409, 503; 422, 503, 500 `{"error"}` | `bundle_summarize` |

The `detail` string behind each 400, 404 and 409 is listed in
[Errors and messages reference](errors-and-messages.md).

| # | Behaviour |
| --- | --- |
| D1 | Streams the upload to `<UPLOAD_FOLDER>/<user_id>/<document_id>.pdf` in 1 MiB chunks. The file is valid when pypdf can count at least one page (`get_pdf_page_count` in `backend/app/services/pdf.py`); the extension and MIME type are not checked. An invalid file is deleted. `sha256_duplicate` is true when the same user already has a document with the same SHA-256; it never blocks the upload. `original_filename` is the upload name passed through `safe_name` (`backend/app/services/files.py`). The document starts in status `uploaded`; no job is started. |
| D2 | Reads every file into memory and merges the readable PDFs in upload order (`merge_pdfs` in `backend/app/services/aggregate.py`); unreadable files are skipped. `records` is `[{filename, start, end, pages}]`, one per merged file. Stores the merged PDF like D1, names the document `name` (trimmed, first 512 characters) or `aggregated-records.pdf`, and creates one review row per source file with category `100`, placeholders `-` and `include` set from category 100's default. Source filenames are not stored. Then enqueues a `classify` job with model `GENAI_MODEL`. |
| D3 | The caller's documents only, newest first. An admin may pass `owner` to list that account's documents instead; for anyone else `owner` is ignored (their own list, never an error). Three SQL queries whatever the count. |
| D4 | `rows` are Editor rows. `categories` is `[{id, name}]` for active categories (`catalog.get_category_options`). `doctors` and `letter_types` come from `reporting.DOCTORS` and `reporting.LETTER_TYPES` in `backend/app/services/reporting.py`. `owner` is `{id, name}` of the account that owns the record, so the workbench can say so when an admin has opened another reviewer's record. |
| D5 | Synchronous model call over pages 1 to min(15, page count) (`extract_header` in `backend/app/services/extraction.py`). A field the extraction found overwrites the stored value; a field it did not find keeps the stored value. The response is the merged, stored view. Nothing is stored on a `PipelineError`. |
| D6 | Writes all nine header fields; a field missing from the body is stored as empty. `letter_type` outside `advocacy`, `interrogatory`, `none` is stored as empty. `pages_received` is stored as a positive integer, or null when blank, non-numeric or not positive. |
| D7 | Only the owner may delete, even an admin (404, as for someone else's record): an admin can open and fix another reviewer's record, but deleting is not fixing. Refused while the document has an active job. Deletes the document with its jobs, review rows, summaries and page texts (ORM cascade), commits, then removes the stored PDF (a failure is logged, not returned). Prepared export files of the document are not touched; they are deleted when their token expires. |
| D8 | Serves the stored PDF with no `Content-Disposition` header, so the browser shows it inline, and with `Cache-Control: no-store`, so no browser or proxy cache keeps a copy of the patient record. |
| D9 | `job` is the progress of the newest job of any kind, or null. When that job is not a summarize job, its `attention` is replaced by the newest summarize job's `attention`. `unreviewed_duplicate_groups` counts clusters with two or more included members and no dismissed member (advisory only). |
| D10 | `clusters` holds groups of two or more rows, members sorted oldest date first. `job` is the newest dedup job's progress, or null. `checked` is true when any dedup job for the document has state `done`. `stale` is true when checked and an included row has no stored `source_text`. `unreadable` counts included rows whose stored text is blank (0 when not checked). `checked` and `stale` come from `duplicate_check_state`, the same function the summarize gate uses. |
| D11 | `fresh: true` first clears `source_text` on every row of the document, so the run re-reads the pages. Enqueues a `dedup` job with model `CLASSIFY_MODEL`. Nothing else starts a dedup job. |
| D12 | `group` is an integer. An unknown group is a 404, checked before the active-job 409. `keep_one`: row `primary_idx` becomes the kept copy and every other member is excluded; the kept copy is included only if any member was included before. `keep_another`: row `idx` is kept as well, with the existing kept copies' inclusion; 400 when no copy is kept yet. `unkeep`: row `idx` stops being kept and is excluded; 400 when it is the last kept copy or not kept. `dismiss`: every member gets `dupe_dismissed = true`, `dupe_primary = false`. `remove_member`: row `idx` leaves the cluster and its `include` is reset to its category's default; if fewer than two members remain, the cluster is dissolved the same way. |
| D13 | Refused while a job is active. Validates with `validate_rows` (`backend/app/services/rows.py`), then deletes every review row and inserts the submitted ones in order. `source_text`, the duplicate fields and `method` carry over only to a row with an unchanged `(start, end)`; the client's `method` is ignored. A removed or changed range clears `dupe_dismissed` on the rest of its cluster. `reopened` is true when the document was `done` or `needs_attention`, the edit left a summary without a matching row or an included row without a summary, and the status was set back to `reviewing`. |
| D14 | 404 unless job `{job_id}` belongs to this document. A job that is no longer `queued`, `running` or `paused` gets 200 with its progress and nothing changes (no audit row). Otherwise sets `cancel_requested`, publishes the Redis cancel flag, and with `force: true` also sends RQ `send_stop_job_command`; a failed stop command is logged, not returned. `graceSeconds` is `JOB_CANCEL_GRACE_SECONDS`. |
| D15 | Enqueues a `segment` job with model `GENAI_MODEL`. `fresh` is accepted and has no effect. |
| D16 | Steps in order: (1) when `rows` is sent, 409 if a job is active, then validate and store them as D13 and audit `rows.edit`; (2) 400 if no row is included; (3) duplicate-check gate: 409 unless a completed, non-stale duplicate check covers the rows, or `skip_duplicate_check` is true, in which case a skip over a missing or stale check is audited; (4) `fresh: true` deletes every summary of the document, reviewer edits included; (5) enqueues a `summarize` job with `model`, or `Settings.model_for("body")` when absent. |
| D17 | Summaries in `idx` order. |
| D18 | 404 when no summary has this `idx`. When `category` is sent it is written to the review row whose `(start, end)` equals the summary's range, before any other field: 409 while any job is active, 409 when no row has that range, 400 when the category is not active, no change when it already matches. Then 409 while a summarize job is active. `summaryTitle` is stored in `edited_title` (first 512 characters), `summaryDate` in `edited_date` (first 16), `summaryText` in `edited_text`; the model output is kept. `excluded` sets whether the summary ships in exports. Only fields present in the body are applied. |
| D19 | Synchronous. 409 while any job is active. Re-summarizes one summary from the live review row with the same range, or from the summary's own snapshot when no row matches, using the current prompt of that category and `model` or `Settings.model_for("body")`. Reuses the row's stored `source_text` when it is not blank. Replaces the model output, verification fields, flags and provenance columns, and clears the reviewer's edits. |
| D20 to D23 | Build the file synchronously, write the audit row, then park the file and answer with a Prepared download (`_offer_download`); the browser fetches it with D26. D20, D21 and D23 answer 409 when every summary is excluded or none exists; D22 does not need summaries. D23 holds the Word MRR, linked PDF, memo and one bundle PDF for each `bundles` entry that matches a row; an entry matching nothing is left out. File contents and names: [Export formats reference](export-formats.md). |
| D24 | Combines the pages of the rows whose category is in `categories` into one PDF, with a cover list when `coverHeading` is set. No model calls. |
| D25 | Synchronous model calls for each matched row, then a Word report. 409 when more rows match than `BUNDLE_SUMMARIZE_CAP` (default 40). Model is `model` or `Settings.model_for("body")`. |

## Downloads router

| # | Method | Path | Auth | Request | Success | Other statuses | Audit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D26 | GET | `/api/documents/{document_id}/downloads/{token}` | Owner | none | 200 file as attachment; 206 for a `Range` request | 404, 503 | `download` |
| D27 | GET | `/api/documents/{document_id}/downloads/{token}/status` | Owner | none | 200 `{state, size}` | 404, 503 | none |

| # | Behaviour |
| --- | --- |
| D26 | Answers 404 `not found` unless the token is 43 characters of `[A-Za-z0-9_-]`, its Redis record exists, it was made by this user for this document, and the file is on disk (`lookup` in `backend/app/services/downloads.py`). Response headers: `Content-Disposition: attachment; filename="<name>"`, `Cache-Control: no-store`, `X-Accel-Buffering: no`, and `Content-Length`. The file can be fetched again until the token expires (`DOWNLOAD_TTL_SECONDS`, default 300). Each GET that sends the file updates the delivery record and writes one log line. 503 `Downloads are unavailable right now. Please try again.` when Redis cannot be reached. |
| D27 | Reads the delivery record (kept `DOWNLOAD_WATCH_SECONDS`, default 900). Same 404 and 503 rules as D26. |

`state` values of D27 (`delivery_status` in `backend/app/services/downloads.py`):

| State | Meaning |
| --- | --- |
| `complete` | The whole file has been sent from its first byte (the API handed over the last byte). |
| `downloading` | A GET is sending the file now. |
| `interrupted` | A GET ran and the file has not been sent whole. |
| `expired` | No GET came and the link has expired. |
| `waiting` | No GET yet and the link is still valid. |

## Admin router

Every admin route also depends on `current_superuser` (`router` in `backend/app/api/admin.py`), in
addition to the gate's 403 for `/api/admin` paths.

| # | Method | Path | Auth | Request | Success | Other statuses | Audit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | GET | `/api/admin/whoami` | Admin | none | 200 `{email, is_admin}` | none | none |
| A2 | GET | `/api/admin/categories` | Admin | none | 200 array of Admin category | none | none |
| A3 | POST | `/api/admin/categories` | Admin | JSON `CategoryCreate` | 201 Admin category | 400 | `category.create` |
| A4 | PATCH | `/api/admin/categories/{category_id}` | Admin | JSON `CategoryUpdate` | 200 Admin category | 400, 404, 409 | `category.update` |
| A5 | GET | `/api/admin/prompts/{category_id}` | Admin | none | 200 `{category_id, text, effective_text, builtin_text, custom}` | none | none |
| A6 | PUT | `/api/admin/prompts/{category_id}` | Admin | JSON `PromptPut` | 200 `{category_id, text, custom: true}` | 400, 404 | `prompt.update` |
| A7 | DELETE | `/api/admin/prompts/{category_id}` | Admin | none | 200 `{category_id, text: null, effective_text, custom: false}` | 404 | `prompt.revert` |
| A8 | POST | `/api/admin/reprocess/{document_id}` | Admin | none | 200 `{"ok": true}` | 400, 404, 409, 503 | `reprocess` |
| A9 | GET | `/api/admin/users` | Admin | none | 200 array of `{id, name, email}` | none | none |

| # | Behaviour |
| --- | --- |
| A2 | Every category, active or not, sorted by numeric id (`catalog.get_categories`). When the `categories` table is empty the built-in constants are listed. Never writes. |
| A3 | `id` must be all digits after trimming and `name` non-empty after trimming. When the `categories` table is empty, writes the built-in categories to it first (`seed_categories` in `backend/app/services/seed_catalog.py`); then refuses an id that already exists. Commits, bumps the catalog revision (`catalog.bump_revision`), audits. |
| A4 | Writes the built-ins first when the table is empty (as A3), then 404 for an unknown id. Applies only the fields present in the body; `id` cannot change. An empty `name` is a 400 and wins over the 409. Setting `active` to false is refused with 409 while any review row, of any owner, uses the category. Commits, bumps the revision, audits. |
| A5 | Accepts any `category_id`. `text` is the custom prompt row or null; `effective_text` is what summaries use now (`catalog.get_prompt`); `builtin_text` is what a revert would restore; `custom` says whether a custom row exists. |
| A6 | Writes the built-ins first when the table is empty (as A3), then 404 for an unknown category. The text is trimmed and must not be empty. Creates the custom row with revision 1, or replaces its text and adds 1 to its revision. Bumps the catalog revision, audits. |
| A7 | Deletes the custom prompt row, so the category resolves its prompt from code again. Bumps the catalog revision, audits. |
| A9 | The active accounts, ordered by name then email, for the records page's "Show records for" choice. Switched-off accounts are left out. Id, name and email only. |
| A8 | Acts on any owner's document (no ownership check). 400 when no review row is included. Enqueues a `summarize` job with `Settings.model_for("body")` and without clearing summaries; the summarize worker keeps every summary whose `(start, end, category)` still matches an included row and summarizes only the rows without one (`_reconcile_summaries` in `backend/app/worker/tasks.py`). The duplicate-check gate of D16 is not applied. |

## Request bodies

### Auth and users (`backend/app/auth/schemas.py`, FastAPI-Users `schemas`)

| Schema | Field | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| Login form | `username` | string | required | The account email. |
| Login form | `password` | string | required | |
| `UserCreate` | `email` | email string | required | |
| `UserCreate` | `password` | string | required | Checked by `UserManager.validate_password`. |
| `UserCreate` | `name` | string | required | Display name; the reviewer name printed in exports. |
| `UserCreate` | `is_active`, `is_superuser`, `is_verified` | boolean | true, false, false | Ignored by the register route. |
| `UserUpdate` | `email` | email string | absent | |
| `UserUpdate` | `password` | string | absent | Validated and hashed. |
| `UserUpdate` | `name` | string | absent | |
| `UserUpdate` | `is_active`, `is_superuser`, `is_verified` | boolean | absent | Applied only by `PATCH /api/users/{id}`. |

### Documents (`backend/app/schemas/documents.py`)

| Schema | Field | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `RowsPayload` | `rows` | array of row objects | `[]` | Validated by `validate_rows`, not by Pydantic. |
| `SummarizeStartPayload` | `rows` | array of row objects or null | null | When present, stored first as in D13. |
| `SummarizeStartPayload` | `model` | string or null | null | Overrides the default summarize model. |
| `SummarizeStartPayload` | `fresh` | boolean | false | Delete all summaries before the run. |
| `SummarizeStartPayload` | `skip_duplicate_check` | boolean | false | Pass the duplicate-check gate; audited. |
| `SummaryEditPayload` | `summaryTitle`, `summaryDate`, `summaryText` | string | absent | Only present fields are written. |
| `SummaryEditPayload` | `excluded` | boolean | absent | |
| `SummaryEditPayload` | `category` | string | absent | Written to the review row, not the summary. |
| `CancelPayload` | `force` | boolean | false | Also kill the RQ work-horse. |
| `DedupStartPayload` | `fresh` | boolean | false | Clear stored `source_text` first. |
| `SegmentStartPayload` | `fresh` | boolean | false | No effect in the current code. |
| `ResummarizePayload` | `model` | string or null | null | |
| `ExportPayload` | `patientName`, `patientdob`, `QMEorAME`, `lawfirm` | string | `""` | Printed in the deliverable. |
| `ExportPayload` | `includePageNumbers` | boolean | false | Append `(Pages X-Y)` to each entry title. |
| `ExportZipPayload` | all `ExportPayload` fields | | | |
| `ExportZipPayload` | `bundles` | array of `ZipBundle` | `[]` | |
| `ZipBundle` | `label` | string or null | null | |
| `ZipBundle` | `categories` | array | `[]` | Category ids; an empty or unmatched entry is left out of the zip. |
| `ZipBundle` | `coverHeading` | string or null | null | Adds a cover list page when set. |
| `ZipBundle` | `downloadName` | string or null | null | Name of the member file. |
| `BundlePayload` | `categories` | array | `[]` | Must be non-empty (400). |
| `BundlePayload` | `label`, `coverHeading`, `downloadName` | string or null | null | As `ZipBundle`. |
| `BundlePayload` | `model` | string or null | null | Used by D25 only. |
| `BundlePayload` | `patientName`, `patientdob`, `QMEorAME`, `lawfirm` | string | `""` | |
| `HeaderPayload` | `patient_first_name`, `patient_last_name`, `patient_dob`, `law_firm`, `attorney_name`, `doctor`, `letter_type`, `letter_date`, `pages_received` | string | `""` | `pages_received` is a string on the wire; see D6. |
| `DuplicateResolvePayload` | `action` | string | required | `keep_one`, `keep_another`, `unkeep`, `dismiss` or `remove_member`. |
| `DuplicateResolvePayload` | `primary_idx` | integer or null | null | Used by `keep_one`. |
| `DuplicateResolvePayload` | `idx` | integer or null | null | Used by `keep_another`, `unkeep` and `remove_member`. |

Row object (items of `rows` in D13 and D16):

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `start`, `end` | integer, or a string or float holding a whole number | required | `1 <= start <= end <= page_count`; rows ascending and non-overlapping; gaps allowed. |
| `category` | string | required | Id of an active category. |
| `title`, `date`, `injury_date`, `flag` | string | `-` | |
| `suggest_merge` | boolean | false | |
| `include` | boolean | true | Whether the row is summarized. |
| `method`, `source_text`, `dupe_group`, `dupe_primary`, `dupe_dismissed` | any | ignored | Server-owned; carried over for an unchanged `(start, end)`. |

### Admin (`backend/app/schemas/admin.py`)

| Schema | Field | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| `CategoryCreate` | `id` | string | required | Digits only; immutable once created. |
| `CategoryCreate` | `name` | string | required | Non-empty after trimming. |
| `CategoryCreate` | `description` | string | `""` | |
| `CategoryCreate` | `examples` | array | `[]` | |
| `CategoryCreate` | `active`, `auto_assign`, `summarize_default` | boolean | true | |
| `CategoryUpdate` | `name`, `description`, `examples`, `active`, `auto_assign`, `summarize_default` | as `CategoryCreate`, or null | absent | Only present fields are applied. No `id`. |
| `PromptPut` | `text` | string | `""` | Must be non-empty after trimming. |

## Response shapes

| Shape | Fields | Built by |
| --- | --- | --- |
| `UserRead` | `id`, `email`, `is_active`, `is_superuser`, `is_verified`, `name` | `backend/app/auth/schemas.py` |
| Document listing | `id`, `original_filename`, `page_count`, `status`, `created_at`, `updated_at`, `active_job` (Job progress or null), `patient_first_name`, `patient_last_name`, `patient_name`, `patient_dob`, `law_firm`, `attorney_name`, `doctor`, `letter_type`, `letter_date`, `pages_received` (integer, or `""` when unset) | `Document.listing()` |
| Editor row | `start`, `end`, `category`, `title`, `date`, `injury_date`, `flag`, `suggest_merge`, `include`, `dupe_group`, `dupe_primary`, `dupe_dismissed`, `source_text`, `ruled_paperwork`, `method` | `ReviewRow.as_row()` plus `_editor_row` in `backend/app/api/documents.py` |
| Job progress | `id`, `kind`, `state`, `stage`, `current`, `total`, `error`, `attention` | `Job.progress()` |
| Duplicate cluster | `group`, `dismissed`, `similarity`, `rows` of `{idx, title, date, pages: {start, end}, include, primary}` | `get_duplicates` |
| Summary | `idx`, `summaryTitle`, `summaryDate`, `summaryText`, `manualCheck`, `excluded`, `edited`, `verified`, `verifyIssues`, `verifyChanged`, `verifyKeptRaw`, `verifyFailed`, `row` (`{start, end, category}`), `rowCategoryLive`, `rowMissing`, `rowMethodLive` | `Summary.listing()` plus `_summary_response` |
| Admin category | `id`, `name`, `description`, `examples`, `active`, `auto_assign`, `summarize_default`, `has_summary_prompt` | `Category.listing()` plus `_category_payload` in `backend/app/api/admin.py` |
| Prepared download | `token`, `filename`, `size` (bytes), `url` (`/api/documents/<document_id>/downloads/<token>`) | `prepare` in `backend/app/services/downloads.py` |

Job `kind`, Job `state` and Document `status` values:
[Job and document states reference](job-and-document-states.md).

## Audit actions

Each row is written to `audit_log` (`audit` in `backend/app/services/audit.py`) with the user id,
the action, the document id where there is one, and `detail`.

| Action | Written by | `detail` |
| --- | --- | --- |
| `upload` | D1 | none |
| `aggregate_upload` | D2 | none |
| `delete` | D7 | none |
| `view_record` | D4, only when an admin opens a record they do not own | none |
| `header.extract` | D5 | `filled=<field names>` (names only, never values) |
| `header.edit` | D6 | `changed=<field names>` (names only, never values) |
| `view_pdf` | D8 | none |
| `dedup.start` | D11 | none |
| `duplicates.resolve` | D12 | `group=<n> action=<action>` |
| `segment.start` | D15 | none |
| `rows.edit` | D13, D16 when `rows` is sent | `rows A->B (merges M, splits S, pages P->Q)` |
| `job.cancel` | D14, active job only | `job <id> kind <kind> state <state> force <bool>` |
| `summarize.skip_duplicate_check` | D16 | `never checked` or `stale check` |
| `summary.category` | D18, category changed | `idx <n> pages <start>-<end>: <old> -> <new>` |
| `summary.edit` | D18, any edit field sent | `idx <n> pages <start>-<end>: <columns joined by +>`, plus `, body chars <delta>` when the text changed |
| `resummarize` | D19 | none |
| `export` | D20 | none |
| `export_pdf` | D21 | none |
| `export_memo` | D22 | none |
| `export_zip` | D23 | none |
| `bundle_pdf` | D24 | none |
| `bundle_summarize` | D25 | none |
| `download` | D26, every GET that finds the file | none |
| `category.create` | A3 | `category <id> active <bool> auto_assign <bool> summarize_default <bool>` |
| `category.update` | A4 | `category <id>: <flag> <old> -> <new>, <text field> changed` for each moved field, or `category <id>: no change` |
| `prompt.update` | A6 | `category <id> prompt created, <n> chars` or `category <id> prompt revision <a> -> <b>, <n> chars` |
| `prompt.revert` | A7 | `category <id> prompt dropped: revision <r>, <n> chars` |
| `reprocess` | A8 | none |

One more action, `segment.rows_replaced`, is written by the segment worker, not by a route
(`backend/app/worker/tasks.py`). No route reads `audit_log`.

## Related pages

- [Errors and messages reference](errors-and-messages.md)
- [Authentication and access](../explanation/auth-and-access.md)
- [How to add an API route or export](../how-to/add-an-api-route-or-export.md)
- [Exports and downloads](../explanation/exports-and-downloads.md)
- [Pipeline and jobs](../explanation/pipeline-and-jobs.md)
- [Configuration reference](configuration.md)
