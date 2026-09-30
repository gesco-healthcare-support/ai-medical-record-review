# backend/app - the application package

Everything that runs in the backend containers. The same package serves the `api` container
(`uvicorn app.main:app`) and both worker containers (`python -m app.worker segment|summarize`).

## Contents

| path | what it is |
| --- | --- |
| `main.py` | The FastAPI app: the deny-by-default auth dependency, `GET /health`, the routers, and the startup sequence (model-server preflight, orphan job recovery, download sweep). |
| `config.py` | Every setting (`Settings`, pydantic-settings), its defaults, the boot guards that refuse unsafe combinations, and the per-stage model and backend resolution. |
| `db.py` | The sync and async engines and session factories, cached per process. |
| `models.py` | The SQLAlchemy models for all 14 tables. |
| `errors.py` | The pipeline error types and the user-facing sentence for each. |
| `cli.py` | `python -m app.cli admin grant|revoke|list <email>` - the only way to make an admin. |
| [`api/`](api/README.md) | The documents, downloads and admin routers. |
| [`auth/`](auth/README.md) | Login, sessions, registration, password hashing (FastAPI-Users). |
| [`schemas/`](schemas/README.md) | Pydantic request and response bodies. |
| [`services/`](services/README.md) | The pipeline: OCR and page text, segmentation, categorization, duplicates, summaries, exports, the model providers. |
| [`worker/`](worker/README.md) | The RQ worker process, the job functions and job lifecycle. |

## Startup order

When the `api` container starts, `main._lifespan`:

1. configures `app.*` logging to stdout at INFO;
2. checks every configured model server (`services/llm/preflight.py` `assert_backends_ready()`) and
   **refuses to start** if one fails - a model destination that fails its check must not receive
   patient data;
3. recovers jobs orphaned by a dead worker (`worker/recovery.py`) - a failure here is logged and
   the app starts anyway;
4. sweeps prepared downloads whose delete timer was lost in a restart - also log-and-continue.

Reference: [Configuration](../../docs/reference/configuration.md),
[Data model](../../docs/reference/data-model.md),
[HTTP API](../../docs/reference/http-api.md),
[Errors and messages](../../docs/reference/errors-and-messages.md).

<!-- reviewed: 2026-09-30 -->
