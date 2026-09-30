# How to add an API route or export

## When you need it

- The web app needs a new operation on a record, or a new admin operation.
- The reviewers need a new downloadable deliverable (a new export).

The backend's routers are in `backend/app/api/`; the rules below keep a new route inside the same
contracts as the 46 existing ones ([HTTP API reference](../reference/http-api.md)).

## Prerequisites

- The backend runs locally and the test database is up:
  [How to run the app locally](run-the-app-locally.md) and
  [How to run the tests](run-the-tests.md).
- You have read [Authentication and access](../explanation/auth-and-access.md), for the gate and the
  ownership check this guide relies on.

## Steps: a document-scoped route

1. **Pick the module.** A route about one record goes in `backend/app/api/documents.py` (prefix
   `/api/documents`); an admin route goes in `backend/app/api/admin.py` (prefix `/api/admin`,
   already admin-only). A new module needs `router = APIRouter(prefix=..., tags=[...])` and an
   `app.include_router(...)` line in `backend/app/main.py`. You do not add any auth code: the
   app-level gate (`enforce_auth`) already refuses anonymous callers on every new path.

2. **Define the request body** in `backend/app/schemas/documents.py` (or `admin.py`) as a Pydantic
   model. Make it optional in the handler (`payload: MyPayload | None = None`) when every field has
   a default, as the existing start and export routes do, so a client may send no body. If a service
   already owns the validation of a field, keep that field loose (`dict` or `Any`) so its 400
   sentences are not pre-empted by a 422, the way `RowsPayload` leaves rows to `validate_rows`.

3. **Write the handler as a sync `def`** with the ownership dependency. FastAPI runs sync handlers in
   its thread pool, so blocking OCR or model work does not block the event loop. Mirror `get_pdf`
   or `delete_document`:

    ```python
    @router.post(
        "/{document_id}/example",
        responses={409: {"description": "A job is running for this document."}},
    )
    def example_route(
        payload: ExamplePayload | None = None,
        document: Document = Depends(get_owned_document),
        session: Session = Depends(get_db),
        user: User = Depends(current_active_user),
    ):
        if document.active_job is not None:
            raise HTTPException(status_code=409, detail=_JOB_RUNNING_DETAIL)
        ...
        audit(session, "example", user.id, document.id)
        return {"ok": True}
    ```

    - The path parameter must be named `document_id`: `get_owned_document` reads a parameter of
      that name. Under any other name FastAPI treats `document_id` as a missing query parameter
      and answers 422.
    - `get_owned_document` answers 404 `not found` for a missing or foreign id. Do not add a 403.
    - Refuse with 409 while a job is active if the route writes rows or summaries; a finishing job
      would overwrite them.

4. **Put every helper above the decorator.** A function placed between `@router...` and the handler
   takes the decorator: FastAPI builds a request model from the helper's signature and the handler
   is never registered (see the docstrings of `_apply_category_edits` in `backend/app/api/admin.py`
   and `_apply_keep_one` in `backend/app/api/documents.py`).

5. **Follow the error contract** ([Errors and messages reference](../reference/errors-and-messages.md)):
    - Raise `HTTPException(status_code, detail="<sentence>")` with a plain-language sentence. The
      web app shows a 400 or 409 `detail` to the reviewer word for word (`humanizeError`), so write
      it for them, and never put a file name, title, patient detail or raw exception text in it.
    - Reuse the existing constants where they fit: `_NOT_FOUND_DETAIL`, `_JOB_RUNNING_DETAIL`,
      `_JOB_ALREADY_RUNNING_DETAIL`.
    - Declare each status in `responses=` so the OpenAPI schema lists it. Declaring 422 replaces
      FastAPI's own description of its validation error, so name both meanings (see
      `bundle_summarize`).
    - When the route calls a model or OCR synchronously, catch `PipelineError` and
      `return _pipeline_error_response(document.id, exc)`, which answers `{"error": user_message}`
      with 422, 503 or 500. A new `PipelineError` subclass needs its status added there;
      `PdfUnreadableError` must stay ahead of `OcrUnavailableError`.

6. **If the route starts a job**, call `enqueue(...)` from `backend/app/services/jobs.py` and map
   `JobConflict` to 409 `_JOB_ALREADY_RUNNING_DETAIL`, as `segment_start` does. Stamp
   `prompt_version=PROMPT_VERSION` and `catalog_revision=catalog.catalog_version(session)`. For a
   summarize job use `get_settings().model_for("body")`, never `summary_model`; the two differ on
   every backend except Gemini. A new job kind also needs the steps in
   [How to add a job kind or stage](add-a-job-kind-or-stage.md).

7. **Audit user actions** with `audit(session, "<action>", user.id, document.id, detail=...)`
   (`backend/app/services/audit.py`):
    - The action is at most 32 characters (`AuditLog.action` is `String(32)`).
    - `detail` holds ids, enum values and counts only - never a title, a file name or any text from
      the record.
    - `audit()` commits the session, so everything pending on it commits too. Call it once your own
      writes are complete.

8. **Log ids only.** Never log `original_filename`, a download file name, a whole download token or
   an email address.

9. **Return a summary through `_summary_response`** if the route returns one. The web app replaces
   its cached summary with whatever a mutation returns, so a bare `listing()` would drop the
   `rowCategoryLive`, `rowMissing` and `rowMethodLive` fields from the page.

10. **Document it.** Add the route to [HTTP API reference](../reference/http-api.md) (route table,
    behaviour, audit action) and any new `detail` sentence to
    [Errors and messages reference](../reference/errors-and-messages.md).

## Steps: a new export

Every export is two requests: a POST that builds and parks the file, and a GET that the browser's
own download manager fetches. The POST never returns the file itself; the `_offer_download`
docstring records why (large Blob downloads were cut short in Chrome on the reviewer host).

1. **Write one builder** that returns bytes, in `backend/app/api/documents.py`, beside
   `_mrr_docx_bytes`, `_linked_pdf_bytes` and `_memo_docx_bytes`. The single route and the zip both
   call it, so the archive cannot drift from the button. Use `_included_summaries(document)` when
   the deliverable needs summaries; it raises the 409 `no summaries to export yet`.

2. **Name the file** with `_deliverable_filename(document, "<suffix>", "<ext>", fallback="<word>")`,
   which gives `Lastname_Firstname_Medical_Records_<suffix>.<ext>` from the stored header.

3. **Add the route**, then audit and hand the bytes to `_offer_download`:

    ```python
    @router.post(
        "/{document_id}/export/example",
        responses={
            409: {"description": "There are no summaries to export yet."},
            503: _DOWNLOAD_STORE_UNAVAILABLE,
        },
    )
    def export_document_example(
        payload: ExportPayload | None = None,
        document: Document = Depends(get_owned_document),
        session: Session = Depends(get_db),
        user: User = Depends(current_active_user),
    ):
        payload = payload or ExportPayload()
        content = _example_bytes(session, document, payload, user)
        audit(session, "export_example", user.id, document.id)
        return _offer_download(content, DOCX_MIMETYPE, _example_filename(document), document, user)
    ```

    `_offer_download` answers `{token, filename, size, url}` and 503 when Redis is down. The GET
    route, the token checks, the `download` audit row and the nginx location already exist for any
    token; nothing else changes on the server side.

4. **Add it to the zip** if it belongs in the archive: append a member in `export_document_zip`.

5. **Call it from the web app** with `downloadFile(path, body, fallbackName)` from
   `frontend/lib/download.ts`, never `apiFetch` and never a Blob. See
   [How to extend the frontend](extend-the-frontend.md).

6. **Document it** in [Export formats reference](../reference/export-formats.md) and
   [HTTP API reference](../reference/http-api.md).

## Tests to add

Add tests beside the existing ones: document routes in `backend/tests/test_documents_api.py`, admin
routes in `backend/tests/test_admin_api.py`, download behaviour in `backend/tests/test_downloads.py`.
They drive the real app over `httpx` against the test Postgres and Redis.

| Test | Pattern to copy |
| --- | --- |
| The success path | The `authed` fixture (a signed-in `(client, user_id)`) and `_upload(client, pages=...)` for a document. |
| Another user gets 404 | `test_idor_other_users_document_is_404`. |
| Anonymous gets 401 | `test_documents_require_auth`. |
| Each error status and its `detail` | Assert `resp.status_code` and `resp.json()["detail"]`; import the `_..._DETAIL` constants rather than copying the text. |
| The audit row | Query `AuditLog` by `action` and `document_id`, as the delete test does. |
| A synchronous model call | Replace the service at the name the router imported, for example `monkeypatch.setattr(documents_api, "summarize_row", boom)`, and assert the `{"error"}` body. |
| An export | `_download(client, url, json=...)` posts, checks the `{token, filename, size, url}` answer and returns the GET of the file. |
| An admin route | The `admin_client` fixture in `backend/tests/test_admin_api.py`. |

Run the file you changed, then the linters (from `backend/`):

```bash
uv run pytest tests/test_documents_api.py -q
```

```bash
uv run ruff check . && uv run ruff format --check .
```

## Verify it worked

1. The new tests pass, including the 404-for-another-user test.

2. The route is registered. From `backend/`, with `backend/.env` in place:

    ```bash
    uv run python -c "from app.main import app; [print(sorted(r.methods), r.path) for r in app.routes if getattr(r, 'methods', None)]"
    ```

    Expected: the new method and path are in the list.

3. With the app running, the route appears in the OpenAPI schema with its declared statuses
   ([HTTP API reference](../reference/http-api.md) shows how to read it).

## If it fails

| Symptom | Cause | Fix |
| --- | --- | --- |
| The app fails to import with a FastAPI field error, or the path runs the wrong function | A helper sits between the decorator and the handler and took the decorator. | Move the helper above the decorator. |
| 422 saying `document_id` is a missing query parameter | The path parameter is not named `document_id`. | Rename it in the path. |
| 401 in a test | The test used `client` instead of `authed`. | Use `authed` (or `admin_client`). |
| 500 with a `DataError` from `audit_log` | The action name is longer than 32 characters. | Shorten it. |
| 503 `Downloads are unavailable right now` in tests | The test Redis is not running. | Start it as in [How to run the tests](run-the-tests.md). |
| The reviewer sees the generic sentence instead of yours | The error body is not `{"detail": "<string>"}` or `{"error": "<string>"}`. | Raise `HTTPException` with a string `detail`. |

To undo, delete the route, its schema and its tests; no migration or proxy change is involved unless
you added one.

## Related pages

- [HTTP API reference](../reference/http-api.md)
- [Errors and messages reference](../reference/errors-and-messages.md)
- [Authentication and access](../explanation/auth-and-access.md)
- [Exports and downloads](../explanation/exports-and-downloads.md)
- [How to add a job kind or stage](add-a-job-kind-or-stage.md)
- [How to extend the frontend](extend-the-frontend.md)
- [How to run the tests](run-the-tests.md)
