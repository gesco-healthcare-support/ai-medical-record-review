# How to run the app locally

Use this when you need the whole application running on your machine: to try a change in the
browser, to run the end-to-end tests, or to reproduce something a reviewer saw. It runs the same
`docker-compose.yml` the server runs; only `.env` differs.

If this is your first time, the [first-day tutorial](../tutorials/first-day.md) walks the same
path with more explanation. What each container is and how it is built is in
[Compose services](../reference/compose-services.md).

## Prerequisites

- Docker with the Compose v2 plugin (Docker Desktop on Windows and macOS).
- A Bash shell (Git Bash on Windows).
- Free host ports 8080 (the app) and 5433 (the app's Postgres).
- For the AI steps: a Google Cloud project with Vertex AI enabled and a service-account key file
  for it. Without them the app runs and only the AI jobs fail.

## Steps

### 1. Choose an environment template

Compose reads the root `.env` only to fill `${VAR}` references inside `docker-compose.yml`. There
are three templates in the repository, for three different uses:

| Template | Copy to | Use it for | What it contains |
| --- | --- | --- | --- |
| `deploy/env.docker.example` | `.env` (repo root) | The container stack - this page | The required secrets, `POSTGRES_PASSWORD`, `ENVIRONMENT` and the four Vertex keys. Every other setting takes the default written in `docker-compose.yml`. |
| `.env.example` | `.env` (repo root) | Tuning a setting | Every tunable key with the reasoning behind its value. It has no `ENVIRONMENT` or `POSTGRES_PASSWORD` line, and it sets `VERTEX_MAX_RPM=20` and `SEGMENT_WINDOW_WORKERS=1`, which differ from the Compose defaults of 60 and 3. Copying it wholesale changes pacing behaviour. |
| `backend/.env.example` | `backend/.env` | Running the API on the host (see below) | Host URLs for the test stack's Postgres and Redis, the secrets, the Vertex keys and `UPLOAD_FOLDER`. |

For the container stack, start from `deploy/env.docker.example` and copy individual lines from
`.env.example` only when you mean to change that setting:

```bash
cp deploy/env.docker.example .env
```

A key set in `.env` reaches the containers only if `docker-compose.yml` names it.
[Configuration model](../explanation/configuration-model.md) explains that rule and
[Configuration](../reference/configuration.md) lists every setting.

### 2. Fill in the required values

| Key | Required | How to set it |
| --- | --- | --- |
| `SECRET_KEY` | Yes. Compose refuses to run without it. | A random value of at least 32 bytes. It signs password-reset and verification tokens. |
| `SECURITY_PASSWORD_SALT` | Yes. Compose refuses to run without it. | Any random value on a fresh database. Every stored password is hashed with it, so choose it once: changing it later makes existing passwords fail. A database migrated from the old Flask app needs that app's exact salt. |
| `ENVIRONMENT` | No (default `dev`) | Keep `dev` locally. `prod` marks the session cookie Secure, which browsers store only over HTTPS (some make an exception for `localhost`), so over plain HTTP sign-in can fail silently; `prod` also refuses to start unless Vertex is on. |
| `POSTGRES_PASSWORD` | No (default `mrr_local_only`) | Used both to create the database and in the connection string. It takes effect only when the Postgres volume is created, so change it before the first start or not at all. |
| `GOOGLE_GENAI_USE_VERTEXAI` | For AI jobs | `true`. |
| `GOOGLE_CLOUD_PROJECT` | For AI jobs | Your Google Cloud project ID. |
| `GOOGLE_CLOUD_LOCATION` | No (default `global`) | Leave `global`. |
| `GOOGLE_APPLICATION_CREDENTIALS` | For AI jobs | `/secrets/vertex-sa.json`, the path inside the containers. |

Generate the two secrets with either command:

```bash
openssl rand -hex 32
```

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Put the service-account key where the containers expect it. `./secrets` is mounted read-only at
`/secrets` in the API and both workers, and git ignores everything in it except `.gitkeep`:

```bash
cp /path/to/your-key.json secrets/vertex-sa.json
```

Check that Compose accepts the file (no output means it does):

```bash
docker compose config --quiet
```

### 3. Build the images

```bash
docker compose build
```

To stamp the build with the current commit, pass `GIT_SHA`. The backend images record it on every
job row; without it the stamp reads `unknown`:

```bash
GIT_SHA=$(git rev-parse --short HEAD) docker compose build
```

### 4. Start Postgres and Redis, then migrate

```bash
docker compose up -d --wait postgres redis
docker compose run --rm api alembic upgrade head
```

Run the migration on every fresh database and after every pull that adds a file under
`backend/alembic/versions/`. The migration runs in a one-off container from the API image, so it
always uses the code you just built.

### 5. Start everything

```bash
docker compose up -d
```

### 6. Open the app

Go to `http://localhost:8080`. The proxy on that port is the only entry point: it sends `/api/` to
the API, `/docs/` to the documentation site and everything else to the frontend.

Create an account at `http://localhost:8080/login?view=register`, then restart the workers so they
pick up the new account (see the table in "If it fails"):

```bash
docker compose restart segment-worker summarize-worker
```

To make the account an admin, see
[How to manage users and admins](../how-to/manage-users-and-admins.md).

## Verify it worked

```bash
docker compose ps
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/login
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/users/me
docker compose exec -T api python -c "from app.config import get_settings; print(get_settings().build_sha)"
```

Expected: every service running (three `segment-worker` and three `summarize-worker` containers),
`200` from the sign-in page, `401` from `/api/users/me` (the API answered through the proxy and
correctly asked for a session), and the commit you built from, or `unknown`.

The API's own health route, `/health`, is not reachable through the proxy (`/health` goes to the
frontend). Probe it inside the container:

```bash
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read())"
```

## Rebuild after a change

The images contain the code; nothing is bind-mounted from your checkout. Editing a file changes
nothing until you rebuild the image that contains it and replace the running containers. Always
pass `--force-recreate` so the containers are replaced from the new image.

| You changed | Rebuild | Then |
| --- | --- | --- |
| Anything under `backend/` | `GIT_SHA=$(git rev-parse --short HEAD) docker compose build api segment-worker summarize-worker` | `docker compose up -d --force-recreate api segment-worker summarize-worker` |
| A new file under `backend/alembic/versions/` | As for `backend/` | `docker compose run --rm api alembic upgrade head` before recreating |
| Anything under `frontend/` | `docker compose build web` | `docker compose up -d --force-recreate web` |
| Anything under `docs/` or `docs-site/` | `docker compose build docs` | `docker compose up -d --force-recreate docs` |
| `deploy/nginx.conf` | Nothing (the file is mounted) | `docker compose up -d --force-recreate proxy` |
| `.env` | Nothing | `docker compose up -d --force-recreate api segment-worker summarize-worker` |
| `docker-compose.yml` | Whatever the change touches | `docker compose up -d` |

Build all three backend services, never only `api`. The API and the summarize workers share the
image `mrr-backend-web`, but the segment workers run a different image, `mrr-backend-classifier`.
Building only `api` leaves the segment workers on the old code while every job they run is still
stamped with a build that looks current.

Recreate the API and both workers together after an `.env` change. Several settings, such as the
Vertex pacing ceilings, must hold the same value in every process that calls the model.

## Optional: run the API on the host

For quick backend iteration you can run the API outside Docker against the test stack's Postgres
and Redis. The API then shares a database with the test suite, which is harmless for a serial test
run but blocks a parallel one (see [How to run the tests](../how-to/run-the-tests.md)).

```bash
docker compose -p mrrtest -f docker-compose.dev.yml up -d --wait postgres redis
cd backend
cp .env.example .env
uv sync --extra docs
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

Edit `backend/.env` after copying it: set `SECRET_KEY` and `SECURITY_PASSWORD_SALT`. The
settings read `.env` from the directory you start the process in, so a host process started in
`backend/` reads `backend/.env`, not the root `.env`.

On the host, OCR needs Tesseract and Poppler installed. Tesseract is found through
`TESSERACT_CMD` or `PATH`; Poppler has no setting and must be on `PATH`. The segment worker also
needs the classifier extra: `uv sync --extra docs --extra classifier`, then
`uv run python -m app.worker segment`. To run the frontend against this API, see
[How to extend the frontend](../how-to/extend-the-frontend.md).

## Windows notes

- Use Git Bash for every command on this page. Docker Desktop provides `docker compose`.
- Git Bash rewrites command arguments that look like absolute POSIX paths, such as
  `/app/uploads`, into Windows paths before Docker sees them. Prefix any command that passes a
  container path with `MSYS_NO_PATHCONV=1`.
- For a host-run API on Windows, start it with `uv run python run_dev.py` from `backend/` instead
  of `uvicorn`. The async Postgres driver cannot run on the event loop uvicorn selects on Windows;
  `backend/run_dev.py` builds a compatible loop itself. It serves on `127.0.0.1:8000` without
  auto-reload.
- In a host `.env`, wrap a value containing backslashes in single quotes, for example
  `TESSERACT_CMD='C:\Program Files\Tesseract-OCR\tesseract.exe'`. Inside double quotes the
  parser turns `\t` into a tab and the path silently breaks. The containers never read
  `TESSERACT_CMD`; Tesseract is installed in the images.
- Do not run the queue workers on a Windows host. They are RQ workers, which run each job in a
  forked child process, and Windows has no `fork()`. On Windows, run anything that needs a
  background job (identification, duplicate check, summarizing) through the Compose stack.

## If it fails

| Symptom | Cause | Fix |
| --- | --- | --- |
| Every `docker compose` command stops with `set SECRET_KEY in .env` or `set SECURITY_PASSWORD_SALT in .env` | The key is missing or empty in the root `.env` | Set it (step 2). |
| Sign-in appears to succeed and you are back at the sign-in page | `ENVIRONMENT=prod` over plain HTTP: the browser did not store the Secure cookie | Set `ENVIRONMENT=dev`, then recreate the API and workers. |
| A job never starts; it stays queued | The account was created after the workers started, so no worker listens on its queue | `docker compose restart segment-worker summarize-worker` |
| Identification or summarizing fails straight away | Vertex settings or the key file are missing or wrong | Check the four Vertex keys and `secrets/vertex-sa.json`, then recreate the API and workers. |
| The API logs errors about a missing column or table | The schema is behind the code | `docker compose run --rm api alembic upgrade head` |
| A code change does not show up | The image was not rebuilt, or the container was not replaced | Rebuild and recreate per the table above. |
| `port is already allocated` for 8080 or 5433 | Another process holds the port | Stop that process. The ports are fixed in `docker-compose.yml`. |

For a job that fails for another reason, see
[How to diagnose a stuck or failed job](../how-to/diagnose-a-stuck-or-failed-job.md).

## Undo

Stop the stack and keep its data:

```bash
docker compose down
```

Stop it and delete the database and the uploaded files as well. This cannot be undone:

```bash
docker compose down -v
```

## Related pages

- [Compose services](../reference/compose-services.md)
- [Configuration](../reference/configuration.md)
- [How to run the tests](../how-to/run-the-tests.md)
- [How to deploy to the server](../how-to/deploy-to-the-server.md)
- [How to back up and restore](../how-to/back-up-and-restore.md)
