# backend/app/api

The FastAPI routers for the domain surface: everything a signed-in reviewer does to a record
(upload, rows, jobs, summaries, exports), the GET half of every export, and the admin JSON API.
Sign-in, registration and user routes live in `../auth/`; request bodies live in `../schemas/`.

| File | Purpose |
| --- | --- |
| `__init__.py` | Package docstring only. |
| `deps.py` | `get_owned_document`: loads a document by id and answers 404 unless it belongs to the caller or the caller is an admin. Every document route depends on it (delete adds its own owner-only check). |
| `documents.py` | 25 routes under `/api/documents`: upload (single and multi-file), list, get, header, delete, PDF, status, duplicates, rows, job start and cancel, summaries, exports and category bundles. Also the shared export builders, `_pipeline_error_response` and `_offer_download`. |
| `downloads.py` | 2 routes: the GET that sends a prepared export file, and its delivery status. `_MeasuredFileResponse` logs each transfer and updates the delivery record. |
| `admin.py` | 9 admin-only routes under `/api/admin`: whoami, users (the active accounts for the records page's "Show records for" list), categories, per-category summary prompts, reprocess. |

## How it is used

- `backend/app/main.py` includes `documents.router`, `downloads.router` and `admin.router`. The
  app-level dependency `enforce_auth` (`../auth/deps.py`) protects every route here.
- uvicorn serves `app.main:app`: the `api` service in `docker-compose.yml`, or
  `uv run uvicorn app.main:app --reload --port 8000` from `backend/` in development.
- Handlers are sync functions on the sync SQLAlchemy session (`get_db` in `../db.py`); FastAPI runs
  them in its thread pool.
- Services under `../services/` do the work; nothing in `services/` imports FastAPI.

## Tests

From `backend/`, with the test Postgres and Redis running and migrated:

```bash
uv run pytest tests/test_documents_api.py tests/test_downloads.py tests/test_download_delivery.py tests/test_admin_api.py -q
```

| Test file | Covers |
| --- | --- |
| `tests/test_documents_api.py` | Every document route, ownership (404 for another user), exports through POST then GET, the synchronous model routes with the model mocked. |
| `tests/test_downloads.py` | The download service and routes: tokens, expiry, another user's token, Redis down. |
| `tests/test_download_delivery.py` | The delivery record and the status states. |
| `tests/test_admin_api.py` | Every admin route, including reprocess on another owner's document. |

## Documentation

- [HTTP API reference](../../../docs/reference/http-api.md)
- [Errors and messages reference](../../../docs/reference/errors-and-messages.md)
- [How to add an API route or export](../../../docs/how-to/add-an-api-route-or-export.md)
- [Authentication and access](../../../docs/explanation/auth-and-access.md)
- [Exports and downloads](../../../docs/explanation/exports-and-downloads.md)
