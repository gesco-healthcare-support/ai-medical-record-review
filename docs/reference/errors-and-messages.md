# Errors and messages reference

Every error body the API returns, every error sentence it can send, the pipeline error classes and
their HTTP statuses, and the sentence the web app shows for each kind of failure.

Source of truth: `backend/app/errors.py`, `backend/app/api/documents.py`
(`_pipeline_error_response` and the `HTTPException` calls), `backend/app/api/admin.py`,
`backend/app/api/downloads.py`, `backend/app/api/deps.py`, `backend/app/auth/deps.py`,
`backend/app/auth/users.py`, `backend/app/services/rows.py`, the FastAPI-Users 15.0.5 routers,
`frontend/lib/api.ts`, `frontend/lib/errors.ts`, `frontend/lib/download.ts` and the callers of
`humanizeError`.

## Response body shapes

There is no response envelope: errors, like successes, are bare bodies.

| Shape | Status | Produced by |
| --- | --- | --- |
| `{"detail": "<sentence>"}` | 400, 401, 403, 404, 409, 503 | `HTTPException` raised by a route, by `get_owned_document` or by `enforce_auth`; and, for a job that could not be queued, the `QueueUnavailable` handler in `backend/app/main.py`. |
| `{"detail": "<ERROR_CODE>"}` | 400 | FastAPI-Users routes (login, register, reset, user update). |
| `{"detail": {"code": "<ERROR_CODE>", "reason": "<sentence>"}}` | 400 | FastAPI-Users, when the password rule fails. |
| `{"detail": "<HTTP reason phrase>"}` | 403, 404, 405 | FastAPI-Users dependencies that raise without a detail (`"Forbidden"`, `"Not Found"`), and Starlette for an unknown path (`"Not Found"`) or method (`"Method Not Allowed"`). |
| `{"detail": [<error object>, ...]}` | 422 | FastAPI request validation. No custom handler is registered (`backend/app/main.py`), so this is FastAPI's default: a list of objects with `type`, `loc`, `msg` and `input`. |
| `{"error": "<user_message>"}` | 422, 503, 500 | `_pipeline_error_response`, on the three routes that call a model synchronously: `POST .../extract-header`, `POST .../summaries/{idx}/resummarize`, `POST .../bundle/summarize`. |
| Redirect, `Location: /login` | 302 | The `AuthRedirect` handler in `backend/app/main.py`, for an unauthenticated request whose `Accept` header contains `text/html` and not `application/json`. |
| `Internal Server Error` as `text/plain` | 500 | Starlette, for any exception no handler catches. |

## Status codes

| Status | Meaning in this API |
| --- | --- |
| 302 | Not signed in, on a browser navigation. |
| 400 | The request is well-formed but refused by a business rule; the `detail` says which. |
| 401 | No session cookie, an expired or deleted session, or an inactive user. |
| 403 | Signed in but not an admin. |
| 404 | The document is missing or belongs to someone else; or the job, summary, duplicate group, category, custom prompt, user or download token is not found; or the path does not exist. |
| 405 | The path exists but not for this method. |
| 409 | A state conflict: a job is running, the duplicate check is missing or stale, nothing to export, no bundle match, bundle over the cap, category in use, summary boundaries changed. |
| 413 | Request body over 500 MB. Answered by nginx (`client_max_body_size 500m` in `deploy/nginx.conf`) before the API sees it. |
| 422 | Request validation failed (`{"detail": [...]}`), or a pipeline error that is a property of the document (`{"error": ...}`). |
| 500 | A pipeline error with no specific mapping (`{"error": ...}`), or an unhandled exception (plain text). |
| 502, 504 | Answered by nginx when the API cannot be reached (for example while it restarts) or does not answer within `proxy_read_timeout 300s` (`deploy/nginx.conf`). |
| 503 | Text recognition is unavailable (`{"error": ...}`), the download store (Redis) cannot be reached (`{"detail": ...}`), or a job could not be handed to the queue (`{"detail": ...}`). |

## `detail` sentences sent by the routes

Route numbers (D1 to D27, A1 to A8) are those of the [HTTP API reference](http-api.md).

| Status | `detail` | Sent by |
| --- | --- | --- |
| 400 | `no PDF uploaded` | D1 |
| 400 | `file is not a readable PDF` | D1 |
| 400 | `no PDFs uploaded` | D2 |
| 400 | `no readable PDFs uploaded` | D2 |
| 400 | `primary_idx is not in this cluster` | D12 (`keep_one`) |
| 400 | `idx is not in this cluster` | D12 (`remove_member`) |
| 400 | `action must be 'keep_one', 'dismiss' or 'remove_member'` | D12 |
| 400 | A row validation sentence (next table) | D13, D16 |
| 400 | `no rows are marked for summarization` | D16 |
| 400 | `unknown category` | D18 |
| 400 | `categories must be a non-empty list` | D24, D25 |
| 400 | `category id must be a positive number` | A3 |
| 400 | `name is required` | A3 |
| 400 | `category <id> already exists` | A3 |
| 400 | `name cannot be empty` | A4 |
| 400 | `prompt text cannot be empty` | A6 |
| 400 | `no reviewed rows to summarize` | A8 |
| 401 | `Not authenticated` | `enforce_auth`, every non-public route |
| 403 | `Admin only` | `enforce_auth`, every `/api/admin` route |
| 403 | `Forbidden` | `/api/users/{id}` routes |
| 404 | `not found` | Every Owner route (`get_owned_document`); D14 (job not on this document); D18, D19 (no summary at `idx`); D26, D27 (download token); A4 (category); A8 (document) |
| 404 | `no such duplicate group` | D12 |
| 404 | `unknown category` | A6 |
| 404 | `this category has no custom prompt` | A7 |
| 404 | `Not Found` | `/api/users/{id}` routes; any unknown path |
| 409 | `a job is running for this document; wait for it` | D7, D12, D13, D19 |
| 409 | `a job is already running for this document` | D11, D15, D16, A8 |
| 409 | `this record has not been checked for duplicates` | D16 |
| 409 | `the documents changed since the last duplicate check` | D16 |
| 409 | `a job is running for this document; wait for it before changing the category` | D18 (`category` sent) |
| 409 | `this summary's sub-document boundaries changed; re-run the segment check before changing its category` | D18 (`category` sent) |
| 409 | `summarization is rewriting these summaries; wait` | D18 |
| 409 | `no summaries to export yet` | D20, D21, D23 |
| 409 | `no matching documents in this record` | D24, D25 |
| 409 | `<N> matching documents exceeds the on-demand limit of <cap>; use the main Summaries flow for a record this large` | D25 |
| 409 | `category <id> is used by <N> sub-document(s) and cannot be deactivated. Move those rows to another category first.` (singular `sub-document` when N is 1) | A4 |
| 503 | `Downloads are unavailable right now. Please try again.` | D20 to D27 |
| 503 | `The job queue is unavailable, so the run could not start. Try again later.` | D2, D11, D15, D16, A8: any route that starts a job, when the queue (Redis) cannot be reached. The job is marked `interrupted`, and the document too if it was mid-run; the cause is logged. |

Row validation sentences (`validate_rows` in `backend/app/services/rows.py`). `<i>` is the 1-based
position of the first failing row; validation stops at the first failure.

| `detail` | Cause |
| --- | --- |
| `no rows to summarize` | The row list is empty or missing. |
| `row <i>: start/end must be integers` | `start` or `end` is missing, a boolean, a fraction, or a string that is not a whole number. |
| `row <i>: pages must satisfy 1 <= start <= end <= <page_count>` | A page is out of range or `start` is after `end`. |
| `row <i>: overlaps or is out of order with the previous row` | `start` is not after the previous row's `end`. |
| `row <i>: unknown category <value>` | The category is not an active category id. `<value>` is printed in Python repr form, for example `'99'`. |

## FastAPI-Users error codes

| Code | Shape | Route | Cause |
| --- | --- | --- | --- |
| `LOGIN_BAD_CREDENTIALS` | string | `POST /api/auth/login` | Unknown email, wrong password, or inactive user. |
| `REGISTER_USER_ALREADY_EXISTS` | string | `POST /api/auth/register` | An account with this email exists. |
| `REGISTER_INVALID_PASSWORD` | object with `reason` | `POST /api/auth/register` | The password rule failed. |
| `RESET_PASSWORD_BAD_TOKEN` | string | `POST /api/auth/reset-password` | The token is malformed, expired, or was issued before the password last changed; or the user is gone or inactive. |
| `RESET_PASSWORD_INVALID_PASSWORD` | object with `reason` | `POST /api/auth/reset-password` | The password rule failed. |
| `UPDATE_USER_EMAIL_ALREADY_EXISTS` | string | `PATCH /api/users/me`, `PATCH /api/users/{id}` | Another account has this email. |
| `UPDATE_USER_INVALID_PASSWORD` | object with `reason` | `PATCH /api/users/me`, `PATCH /api/users/{id}` | The password rule failed. |

`reason` is built by `UserManager.validate_password` (`backend/app/auth/users.py`): the text
`Password must contain ` followed by the failed parts, joined by `, `, from `at least 8 characters`,
`a number` and `a symbol`. Example: `Password must contain a number, a symbol`.

## Pipeline error classes

`PipelineError` and its subclasses (`backend/app/errors.py`) carry two texts: `str(exc)` holds the
technical detail for logs, and the class attribute `user_message` is the only text a user sees. On
the three synchronous model routes, `_pipeline_error_response` logs
`pipeline error on document <id>: <technical detail>` at WARNING and answers
`{"error": user_message}` with the status below. It tests `PdfUnreadableError` before
`OcrUnavailableError` because the first is a subclass of the second.

| Class | Parent | Status on sync routes | Meaning | `user_message` |
| --- | --- | --- | --- | --- |
| `PipelineError` | `Exception` | 500 | Base class. | Something went wrong while processing this document. Please try again; if it keeps failing, contact your administrator. |
| `OcrUnavailableError` | `PipelineError` | 503 | Tesseract or Poppler is missing or unreachable, so no page can be read. | Text recognition (OCR) is unavailable on the server, so this document could not be read. Please contact your administrator. |
| `PdfUnreadableError` | `OcrUnavailableError` | 422 | The PDF cannot be opened: corrupt, encrypted, truncated or missing from disk. | This PDF could not be opened - it may be corrupt, password-protected, or incomplete. Try re-uploading the file; if it opens normally elsewhere, contact your administrator. |
| `EmptyExtractionError` | `PipelineError` | 422 | Text recognition ran and found no text on the pages. | No readable text was found in this document, so there was nothing to summarize. The pages may be blank or scanned images the text recognizer could not read. |
| `TranscriptPagesUnreadableError` | `PipelineError` | 422 | The printed page numbers of a deposition transcript could not be read (the model reply was truncated). | The page numbers printed on this transcript could not be read, so no page citations were written for it. Please try again; if it keeps happening, the scan may be too unclear for those numbers to be read. |
| `PipelineTimeoutError` | `PipelineError` | 500 | A pipeline stage ran past its wall-clock budget and was stopped. | Processing took too long and was stopped. Please try again; if it keeps happening the document may be very large or the AI service may be busy. |

An exception that is not a `PipelineError` is not caught by these routes and becomes a plain-text
500.

## Job and row failure messages

A failed background job stores `errors.user_facing_message(exc)` in `Job.error`, which the API
returns as `error` in Job progress (`backend/app/worker/tasks.py`). The per-row reasons in a
`needs_attention` job's `attention` use the same function (`reason_for` in
`backend/app/worker/failures.py`). The summarize give-up path appends a sentence to the message.

`user_facing_message` picks the first match:

| Exception | Message constant |
| --- | --- |
| A `PipelineError` | The class's `user_message` (table above). |
| google-genai `ServerError` with code 504 | `AI_DEADLINE_MESSAGE` |
| Any other google-genai `ServerError` (5xx) | `AI_BUSY_MESSAGE` |
| google-genai `ClientError` with code 429, not a daily quota | `AI_BUSY_MESSAGE` |
| google-genai `ClientError` whose text contains `PerDay` or `free_tier` | `AI_DAILY_QUOTA_MESSAGE` |
| Any other google-genai `ClientError` | `AI_REJECTED_MESSAGE` |
| SQLAlchemy `DataError` whose text contains `too long` | `AI_OVERSIZED_VALUE_MESSAGE` |
| Anything else | `GENERIC_USER_MESSAGE` |

| Constant | Text |
| --- | --- |
| `GENERIC_USER_MESSAGE` | Something went wrong while processing this document. Please try again; if it keeps failing, contact your administrator. |
| `AI_BUSY_MESSAGE` | The AI service was busy and the request could not be completed. Please try again shortly. |
| `AI_DAILY_QUOTA_MESSAGE` | The daily AI quota has been used up; it resets on Google's schedule. |
| `AI_REJECTED_MESSAGE` | The AI service rejected the request (a permission or request problem). |
| `AI_DEADLINE_MESSAGE` | One part of this document needed longer than the current per-request time limit allows. Please contact your administrator, who can raise the limit or split the document. |
| `AI_OVERSIZED_VALUE_MESSAGE` | The AI generated a value too long to store, so this document could not be completed. Please contact your administrator. |

How a stored `Job.error` maps back to a failure category (`job_outcome` in
`backend/app/worker/failures.py`): [How to diagnose a stuck or failed job](../how-to/diagnose-a-stuck-or-failed-job.md).

## How the web app reads an error

`errorFromResponse` (`frontend/lib/api.ts`) turns a failed response into an `ApiError` with the
HTTP status and one message:

| Body | `ApiError` message |
| --- | --- |
| `detail` is a string | That string. |
| No string `detail`, `error` is a string | That string. |
| Anything else: a validation list, a `{code, reason}` object, plain text, no body | The synthesized `<path> failed (<status>)`. |

`apiFetch` (`frontend/lib/api.ts`) adds three rules: a transport failure throws
`ApiError("network", 0)`; a 401 calls `signedOut()`, which navigates to `/login` unless the page is
already there, and throws `ApiError("signed out", 401)`; a 204 resolves to `null`. It always sends
`Accept: application/json`, so the API answers 401 rather than 302.

The query client (`frontend/app/providers.tsx`) also navigates to `/login` on any query or mutation
that fails with 401, and retries a failed query once unless the status is 401, 403 or 404.

## Sentences the web app shows

`humanizeError(err, ctx)` (`frontend/lib/errors.ts`) turns any thrown error into one sentence,
checking in this order:

| Condition | Sentence |
| --- | --- |
| Not an `ApiError` | `ctx.fallback`, else the generic sentence |
| Status 0 (no response) | Couldn't reach the server. Check your connection and try again. |
| 401 | Your session has ended. Please sign in again. |
| 403 | You don't have permission to do that. |
| 404 | `ctx.notFound`, else: This item is no longer available - it may have been deleted or moved. Refresh and try again. |
| Message is the synthesized `... failed (<status>)` | `ctx.fallback`, else the generic sentence |
| Any other status (400, 409, 422, 500, 503 with a message) | The server's message, verbatim |

The generic sentence: Something went wrong on our end. Please try again; if it keeps failing,
contact your administrator.

So a `detail` string on 400 or 409 and an `{"error"}` message on 422, 500 or 503 reach the reviewer
word for word. A 422 validation list, a FastAPI-Users `{code, reason}` object and a plain-text 500
reach the reviewer as the call site's fallback.

`ctx.notFound` overrides:

| File | 404 sentence |
| --- | --- |
| `frontend/components/documents/documents-view.tsx` | That record is no longer available - it may have been deleted. Refresh the list. |
| `frontend/components/admin/admin-view.tsx` | That item is no longer available - refresh and try again. |
| `frontend/components/bundle/bundle-page-client.tsx` | That record is no longer available. |
| `frontend/hooks/use-review-workflow.ts` | This record is no longer available to you - it may have been moved or deleted. Go back and refresh. |

`ctx.fallback` per call site:

| File | Action | Fallback |
| --- | --- | --- |
| `frontend/components/documents/documents-view.tsx` | Upload | Upload failed. |
| `frontend/components/documents/documents-view.tsx` | Start identification | Could not start identification. |
| `frontend/components/documents/documents-view.tsx` | Delete | Could not delete the record. |
| `frontend/components/documents/split-upload-dialog.tsx` | Combine several PDFs | Could not combine the records. |
| `frontend/components/admin/admin-view.tsx` | Update a category | Could not update the category. |
| `frontend/components/admin/admin-view.tsx` | Reprocess | Could not reprocess that record. |
| `frontend/components/admin/category-dialog.tsx` | Save a category | Could not save the category. |
| `frontend/components/admin/prompt-dialog.tsx` | Save a prompt | Could not save the prompt. |
| `frontend/components/admin/prompt-dialog.tsx` | Revert a prompt | Could not revert the prompt. |
| `frontend/components/review/export-dialog.tsx` | Export | Export failed. |
| `frontend/components/review/header-bar.tsx` | Save the header | Could not save the header. |
| `frontend/components/review/header-bar.tsx` | Detect the header | Could not read the header. |
| `frontend/components/review/duplicates-view.tsx` | Resolve a cluster | Could not save - please try again. |
| `frontend/components/review/duplicates-view.tsx` | Load duplicates | Could not load duplicates. |
| `frontend/components/review/review-page-client.tsx` | Start or re-run the duplicate check | Could not start the check - please try again. |
| `frontend/components/review/summaries-view.tsx` | Load summaries | Could not load summaries. |
| `frontend/components/review/summaries-view.tsx` | Save an edit, include or exclude | `Not saved: ` followed by the sentence, fallback `please try again` |
| `frontend/components/review/summaries-view.tsx` | Change a category | `Category not saved: ` followed by the sentence, fallback `please try again` |
| `frontend/components/review/summaries-view.tsx` | Re-draft | `Re-draft failed: ` followed by the sentence, fallback `please try again` |
| `frontend/hooks/use-review-workflow.ts` | Row autosave | `Not saved: ` followed by the sentence, fallback `please try again` |
| `frontend/hooks/use-review-workflow.ts` | Stop a run | could not stop the run |
| `frontend/hooks/use-review-workflow.ts` | Restart a stopped run | could not restart the run |
| `frontend/hooks/use-review-workflow.ts` | Identification run | identification failed |
| `frontend/hooks/use-review-workflow.ts` | Summarization run | summarization failed |
| `frontend/hooks/use-review-workflow.ts` | Load the record | `Could not load this document: ` followed by the sentence, fallback `error` |
| `frontend/hooks/use-review-workflow.ts` | Start identification | Could not start identification. |
| `frontend/hooks/use-review-workflow.ts` | Start summarization | Could not start summarization. |
| `frontend/components/bundle/bundle-page-client.tsx` | Bundle PDF | The download failed. |
| `frontend/components/bundle/bundle-page-client.tsx` | Bundle report | The report failed. |
| `frontend/components/bundle/bundle-page-client.tsx` | Detect the header | Could not read the header. |
| `frontend/components/bundle/bundle-page-client.tsx` | Load a record's detail | Something went wrong reading it. |

## Sign-in and account pages

The auth forms in `frontend/components/auth/` write their own sentences and call `humanizeError`
only when `isOffline(err)` is true (status 0).

| Form | Failure | Sentence |
| --- | --- | --- |
| `sign-in-form.tsx` | Any failure except offline | We couldn't sign you in. Check your email and password, then try again. |
| `register-form.tsx` | Any 400 | An account with this email already exists. |
| `register-form.tsx` | Any other failure except offline | Could not create your account. Check your details and try again. |
| `forgot-form.tsx` | Any server answer, success or failure | The "Check your email" screen: If an account exists for `<address>`, we sent a link to reset your password. |
| `reset-form.tsx` | Any failure except offline | This reset link is invalid or has expired. Request a new one. |
| `reset-form.tsx` | No `token` in the URL | Title "Link expired": This password reset link is invalid or has expired. |

All four show the offline sentence (Couldn't reach the server...) when the request never reached the
server.

## Download failures

Exports go through `downloadFile` (`frontend/lib/download.ts`), which throws `ApiError` like
`apiFetch` and adds two sentences of its own:

| Condition | Sentence |
| --- | --- |
| 502 or 504 with no server message | The server did not finish preparing the file. Please try again. (`DOWNLOAD_NOT_PREPARED`) |
| A 2xx answer whose JSON body cannot be read | The download was interrupted before it finished. Please try again. (`DOWNLOAD_INTERRUPTED`) |

Each failure is also written to the browser console as `download failed` with the phase, the status
and the declared size - never the file name.

## Related pages

- [HTTP API reference](http-api.md)
- [Authentication and access](../explanation/auth-and-access.md)
- [How to add an API route or export](../how-to/add-an-api-route-or-export.md)
- [How to diagnose a stuck or failed job](../how-to/diagnose-a-stuck-or-failed-job.md)
- [Exports and downloads](../explanation/exports-and-downloads.md)

<!-- reviewed: 2026-09-30 -->
