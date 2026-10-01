# backend/app/schemas

Pydantic request bodies for the domain routers in `../api/`. They only describe what a client may
send; responses are plain dicts built by model methods such as `Document.listing()`,
`Summary.listing()`, `Job.progress()` and `Category.listing()` in `../models.py`, plus helpers in
the routers. The user schemas used by FastAPI-Users (`UserRead`, `UserCreate`, `UserUpdate`) live in
`../auth/schemas.py`.

| File | Purpose |
| --- | --- |
| `__init__.py` | Package docstring only. |
| `documents.py` | Bodies for `/api/documents` routes: `RowsPayload`, `SummarizeStartPayload`, `SummaryEditPayload`, `CancelPayload`, `DedupStartPayload`, `SegmentStartPayload`, `ResummarizePayload`, `ExportPayload`, `BundlePayload`, `HeaderPayload`, `ZipBundle`, `ExportZipPayload`, `DuplicateResolvePayload`. |
| `admin.py` | Bodies for `/api/admin` routes: `CategoryCreate`, `CategoryUpdate`, `PromptPut`. |

## How it is used

- A router declares a body parameter with one of these types; FastAPI validates it and answers 422
  with a `{"detail": [...]}` list when the shape is wrong.
- Business rules (a numeric category id, a non-empty name, valid row ranges) are checked in the
  routes or services and answered with a 400 sentence, not in these models.
- Edit bodies are applied with `model_dump(exclude_unset=True)`, so only the fields a client
  actually sent are written.

## Tests

The schemas are exercised through the route tests. From `backend/`, with the test Postgres and
Redis running and migrated:

```bash
uv run pytest tests/test_documents_api.py tests/test_admin_api.py -q
```

## Documentation

- [HTTP API reference](../../../docs/reference/http-api.md) (the "Request bodies" section lists
  every field)
- [Errors and messages reference](../../../docs/reference/errors-and-messages.md)
- [How to add an API route or export](../../../docs/how-to/add-an-api-route-or-export.md)

<!-- reviewed: 2026-09-30 -->
