# backend/app/worker

The RQ worker tier: the processes that run the long pipeline jobs (segment, classify, dedup,
summarize) outside the web process. The API creates a `jobs` row and enqueues only its id on Redis;
a worker started with `python -m app.worker <queue>` takes the id from the owner's lane, runs the
task through one shared runner, and writes progress, results and the terminal state to Postgres.
This package also holds the cancel channel, the failure taxonomy, the RQ callbacks that finalize a
killed or abandoned job, and the orphan recovery the API runs at start-up.

## Files

| File | What it holds |
| --- | --- |
| `__init__.py` | Package docstring: the two queues, and which worker needs torch. |
| `__main__.py` | Entry point `python -m app.worker [segment] [summarize]`: model-backend preflight, lane expansion, `RoundRobinWorker` with the scheduler on. |
| `queues.py` | Kind -> base queue and kind -> task dotted path; per-user lane names; the lazy Redis connection. Import-light so the API can enqueue without torch. |
| `tasks.py` | The runner `_run`, the `_finalize_*` functions, and the four task functions `segment_document`, `classify_document`, `dedup_document`, `summarize_document`. |
| `cancel.py` | The Redis cancel flag `mrr:cancel:<job_id>` and this process's current job id. |
| `failures.py` | `classify_failure` (transient or permanent), `job_outcome` / `is_failure` for stored jobs, and the control signals `JobPaused`, `JobCancelled`, `JobNeedsAttention`. |
| `finalizers.py` | RQ `on_stopped` / `on_failure` callbacks, run in the worker parent. |
| `recovery.py` | `recover_orphans`, called once from the API's start-up. |

The job service that creates and dispatches jobs (`create_job`, `enqueue`, `mark_terminal`, the
status maps and `ACTIVE_STATES`) is `backend/app/services/jobs.py`, one folder over.

## How it runs

In the compose stack, two services run this package (`docker-compose.yml`):

| Service | Image | Command |
| --- | --- | --- |
| `segment-worker` (3 replicas) | `mrr-backend-classifier`, built with `--extra docs --extra classifier` | `python -m app.worker segment` |
| `summarize-worker` (3 replicas) | `mrr-backend-web`, built with `--extra docs` | `python -m app.worker summarize` |

With no arguments the entry point serves both queues. RQ's `RoundRobinWorker` forks a work-horse
per job with `os.fork()`, which Windows does not have, so run workers in the containers (or on a
Linux or macOS host with Redis and Postgres reachable and the backend settings in the environment).

Workers list their per-user lanes once, at start-up. Restart both worker services after adding a
user:

```bash
docker compose restart segment-worker summarize-worker
```

Worker logs go to stdout at INFO and carry ids and counts only:

```bash
docker compose logs --since 1h segment-worker summarize-worker
```

## How it is tested

The tests run against a real Postgres and Redis (see the linked page on running the tests). From
`backend/`:

```bash
uv run pytest -q tests/test_jobs.py tests/test_cancel.py tests/test_cancel_escapes_model_calls.py tests/test_failures.py tests/test_job_health.py tests/test_main.py
```

## Documentation

- [Pipeline and jobs](../../../docs/explanation/pipeline-and-jobs.md)
- [Job and document states reference](../../../docs/reference/job-and-document-states.md)
- [How to diagnose a stuck or failed job](../../../docs/how-to/diagnose-a-stuck-or-failed-job.md)
- [How to add a job kind or stage](../../../docs/how-to/add-a-job-kind-or-stage.md)
- [OCR and page text](../../../docs/explanation/ocr-and-page-text.md)
- [How to run the tests](../../../docs/how-to/run-the-tests.md)
