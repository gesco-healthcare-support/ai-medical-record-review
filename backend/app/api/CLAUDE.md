# backend/app/api - agent instructions

HTTP routers for documents, downloads and admin. Full contracts:
`docs/reference/http-api.md` and `docs/reference/errors-and-messages.md`.

## Rules that must hold

- Do not add auth code for "must be signed in". The app-level gate `enforce_auth`
  (`app/auth/deps.py`) already covers every route. A public route is made public only by adding
  its exact path to `_PUBLIC_EXACT` there.
- Any route with a record id takes `document: Document = Depends(get_owned_document)` and the path
  parameter is named exactly `document_id` (the dependency reads that name). A missing record, or
  another user's record for a non-admin, is 404 `not found`, never 403; an admin passes (they may
  open and fix any reviewer's record), except on delete, which stays owner-only.
  `admin.reprocess` is the only route without ownership.
- Handlers are sync `def` on `Depends(get_db)` (sync session). Do not switch them to `async def`
  or to the async session; the async session is for FastAPI-Users only.
- Define every helper ABOVE the route decorator. A function placed between `@router...` and the
  handler takes the decorator and the handler never registers.
- Errors: `HTTPException(status, detail="<plain sentence>")`. The web app shows a 400 or 409
  `detail` to the reviewer verbatim, so no file names, titles, patient data or exception text.
  Reuse `_NOT_FOUND_DETAIL`, `_JOB_RUNNING_DETAIL`, `_JOB_ALREADY_RUNNING_DETAIL`. Declare every
  non-2xx status in `responses=`. No response envelope.
- Synchronous model or OCR calls: `except PipelineError as exc: return
  _pipeline_error_response(document.id, exc)`. In that function `PdfUnreadableError` must be
  tested before `OcrUnavailableError` (it is a subclass).
- A route that writes rows or summaries answers 409 while `document.active_job` is not None.
- Starting a job: `enqueue(...)` from `app/services/jobs.py`, catch `JobConflict` -> 409
  `_JOB_ALREADY_RUNNING_DETAIL`. Summarize model is `get_settings().model_for("body")`, never
  `summary_model`. Stamp `prompt_version=PROMPT_VERSION`, `catalog_revision=catalog.catalog_version(session)`.
- Audit user actions with `audit(session, action, user.id, document.id, detail=...)`. Action is at
  most 32 characters. `detail` is ids, enum values and counts only. `audit()` commits the session.
- Every route that returns a summary returns `_summary_response(document, summary)`, not
  `summary.listing()`; the client replaces its cached item with the response.
- Exports: one bytes builder shared by the single route and `export_document_zip`; file name from
  `_deliverable_filename`; audit; then `return _offer_download(...)`. Never return the file from
  the POST. Declare `503: _DOWNLOAD_STORE_UNAVAILABLE`.
- Keep `Cache-Control: no-store` and `X-Accel-Buffering: no` on the download GET.
- Logging is ids only. Never log `original_filename`, a download file name, a whole download token
  or an email. Download log lines carry the first 8 characters of the token.
- Admin catalog writes: `seed_categories(session)` first, then the write, `session.commit()`,
  `catalog.bump_revision(session)`, `audit(...)`. `GET /api/admin/categories` must never write.
  Never call `seed_catalog()` from app code.

## Traps that have bitten before

- `_store_rows` deletes and re-inserts every row. `source_text`, the dupe fields and `method`
  survive only for an unchanged `(start, end)`; the client's `method` is ignored on purpose.
- Summaries bind to rows by exact `(start, end)`. `PUT /rows` can move a `done` or
  `needs_attention` document back to `reviewing` (`_reopen_if_summaries_stranded`); keep that.
- `extract-header` must not overwrite a stored header field with an empty detection. `PUT /header`
  is the only path that may blank a field.
- Category deactivation is refused while any review row uses it, and the empty-name 400 must win
  over that 409 (`_apply_category_edits` runs first).
- `list_documents` needs `selectinload(Document.jobs)`; without it the listing issues one query per
  document.
- `duplicate_check_state` is shared by the Duplicates payload and the summarize gate. Do not fork a
  second definition.
- A FastAPI 422 validation list is not shown to the reviewer (the frontend reads `detail` only when
  it is a string). If a rule must reach the reviewer, check it in the route and raise a 400.
- Declaring 422 in `responses=` replaces FastAPI's own description of it; name both meanings.

## Commands

From `backend/`, with the test Postgres and Redis up and migrated (`docs/how-to/run-the-tests.md`):

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/test_documents_api.py tests/test_downloads.py tests/test_download_delivery.py tests/test_admin_api.py -q
```

## When you change a route

Update in the same change: the route table, behaviour row and audit table in
`docs/reference/http-api.md`; any new `detail` sentence in `docs/reference/errors-and-messages.md`;
a new export in `docs/reference/export-formats.md`. Procedure:
`docs/how-to/add-an-api-route-or-export.md`.
