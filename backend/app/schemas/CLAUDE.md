# backend/app/schemas - agent instructions

Pydantic request bodies for `app/api/`. Field-by-field reference: the "Request bodies" section of
`docs/reference/http-api.md`.

## Rules that must hold

- Request bodies only. Responses are dicts from model methods (`Document.listing()`,
  `Summary.listing()`, `Job.progress()`, `Category.listing()`) and router helpers; do not introduce
  response models here without changing every route that builds those dicts.
- User schemas belong in `app/auth/schemas.py`, not here.
- Give every field a default unless the route cannot work without it. A client that predates a new
  field must keep working (the contract stated in the `HeaderPayload` and `BundlePayload`
  docstrings).
- Row payloads stay `list[dict[str, Any]]`. `services.rows.validate_rows` owns row validation and
  its 400 sentences; a strict row model would turn them into a 422 list that the web app cannot
  show the reviewer.
- Keep business checks (numeric id, non-empty after trim, known category, non-empty category list)
  in the routes as 400s, not in validators.
- `HeaderPayload.pages_received` is a string on the wire; the route coerces it. Do not type it as
  `int` - an empty box must mean "unset", and a 422 would lose the rest of the header.
- `CategoryUpdate` has no `id` (ids are immutable). `CategoryUpdate` and `SummaryEditPayload` are
  applied with `model_dump(exclude_unset=True)`: every field must stay optional so an absent field
  is not written.
- Field names are the wire contract with the web app, including the camelCase ones
  (`summaryTitle`, `patientdob`, `QMEorAME`, `includePageNumbers`, `coverHeading`,
  `downloadName`). Renaming one breaks `frontend/lib/review-api.ts`, `frontend/lib/bundle-api.ts`,
  `frontend/hooks/use-summaries.ts` or the components that post it. Search `frontend/` first.
- `SegmentStartPayload.fresh` is accepted and has no effect yet; keep it so the UI can send the same
  Continue / Start over pair for every job kind.

## Commands

From `backend/`, with the test Postgres and Redis up and migrated (`docs/how-to/run-the-tests.md`):

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/test_documents_api.py tests/test_admin_api.py -q
```

## Docs to update with a change here

The "Request bodies" tables in `docs/reference/http-api.md`.
