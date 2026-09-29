# Compose services reference

Every service, image, volume, port and proxy route defined by the two Compose files.

Source of truth: `docker-compose.yml`, `docker-compose.dev.yml`, `deploy/nginx.conf`,
`backend/Dockerfile`, `frontend/Dockerfile`, `docs-site/Dockerfile`.

## Stacks

| File | Project name | Purpose | Services |
| --- | --- | --- | --- |
| `docker-compose.yml` | `mrr` (set by `name:`) | The whole application. The same file runs on developer machines and on the server; only `.env` differs. | `postgres`, `redis`, `api`, `segment-worker`, `summarize-worker`, `web`, `proxy`, `docs` |
| `docker-compose.dev.yml` | Not set. Compose uses the checkout folder's name unless `-p` is given; the documented form is `-p mrrtest`. | Test and host-development dependencies only. | `postgres`, `redis` |

## App stack services (`docker-compose.yml`)

| Service | Image | Build | Command | Host ports | Volumes and mounts | Healthcheck | depends_on | Replicas | Restart |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `postgres` | `postgres:16` (pulled) | - | Image default | `5433:5432` | `mrr_pgdata:/var/lib/postgresql/data` | `pg_isready -U mrr -d mrr`, every 5 s, timeout 3 s, 10 retries | - | 1 | `unless-stopped` |
| `redis` | `redis:7` (pulled) | - | `redis-server --save "" --appendonly no` | None | None | None | - | 1 | `unless-stopped` |
| `api` | `mrr-backend-web` | Context `./backend`; args `UV_EXTRAS="--extra docs"`, `GIT_SHA=${GIT_SHA:-unknown}` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | None | `mrr_uploads:/app/uploads`; `./instance:/app/instance:ro`; `./secrets:/secrets:ro` | None | `postgres` (`service_healthy`), `redis` (`service_started`) | 1 | `unless-stopped` |
| `segment-worker` | `mrr-backend-classifier` | Context `./backend`; args `UV_EXTRAS="--extra docs --extra classifier"`, `GIT_SHA=${GIT_SHA:-unknown}` | `python -m app.worker segment` | None | `mrr_uploads:/app/uploads`; `./secrets:/secrets:ro` | None | `postgres` (`service_healthy`), `redis` (`service_started`) | 3 (`deploy.replicas`) | `unless-stopped` |
| `summarize-worker` | `mrr-backend-web` | Context `./backend`; args `UV_EXTRAS="--extra docs"`, `GIT_SHA=${GIT_SHA:-unknown}` | `python -m app.worker summarize` | None | `mrr_uploads:/app/uploads`; `./secrets:/secrets:ro` | None | `postgres` (`service_healthy`), `redis` (`service_started`) | 3 (`deploy.replicas`) | `unless-stopped` |
| `web` | `mrr-frontend` | Context `./frontend` | Image default: `node server.js` | None | None | None | `api` | 1 | `unless-stopped` |
| `proxy` | `nginx:1.30` (pulled) | - | Image default | `8080:80` | `./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro` | None | `api`, `web`, `docs` | 1 | `unless-stopped` |
| `docs` | `mrr-docs` | Context `.` (repository root); Dockerfile `docs-site/Dockerfile` | Image default (nginx) | None | None | None | - | 1 | `unless-stopped` |

No service defines `profiles`, networks, resource limits or a `stop_grace_period`; all services
share the project's default network and reach each other by service name.

### Environment by service

| Service | Environment |
| --- | --- |
| `postgres` | `POSTGRES_USER=mrr`, `POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-mrr_local_only}`, `POSTGRES_DB=mrr` |
| `redis` | None |
| `api`, `segment-worker`, `summarize-worker` | The shared `x-backend-env` block (next section) |
| `web` | `NODE_ENV=production`, `API_ORIGIN=http://api:8000` |
| `proxy` | None |
| `docs` | None |

### The shared backend environment (`x-backend-env`)

`api` and both workers receive one environment block, defined once as the YAML anchor
`x-backend-env` and applied with `environment: *backend-env`. Compose reads the root `.env` only to
substitute `${VAR}` references in this file, so a setting reaches these containers only if the
block names it.

Values fixed by the block:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://mrr:${POSTGRES_PASSWORD:-mrr_local_only}@postgres:5432/mrr` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `UPLOAD_FOLDER` | `/app/uploads` |
| `OMP_THREAD_LIMIT` | `1` |
| `OMP_NUM_THREADS` | `1` |

Variables that must be set in `.env` (Compose stops with an error when either is unset or empty):

| Variable | Error text |
| --- | --- |
| `SECRET_KEY` | `set SECRET_KEY in .env (openssl rand -hex 32)` |
| `SECURITY_PASSWORD_SALT` | `set SECURITY_PASSWORD_SALT in .env (the real Flask salt)` |

Every other variable in the block is written `NAME: ${NAME:-default}`, so `.env` may override it
and the Compose default applies otherwise. Inside a container that default, not the one in
`backend/app/config.py`, is the value in force. Each of these settings is listed with its default
in [Configuration](../reference/configuration.md).

`TESSERACT_CMD` is deliberately not in the block: it holds a Windows host path, and the images have
Tesseract on `PATH`.

## Test stack services (`docker-compose.dev.yml`)

| Service | Image | Command | Host ports | Volumes | Healthcheck | Environment | Restart |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `postgres` | `postgres:16` | Image default | `5432:5432` | `mrr_pgdata:/var/lib/postgresql/data` | `pg_isready -U mrr -d mrr`, every 5 s, timeout 3 s, 10 retries | `POSTGRES_USER=mrr`, `POSTGRES_PASSWORD=mrr_dev_only` (literal), `POSTGRES_DB=mrr` | None |
| `redis` | `redis:7` | `redis-server --save "" --appendonly no` | `6379:6379` | None | None | None | None |

No builds, no `depends_on`, no replicas settings. `backend/tests/conftest.py` reads both Compose
files to find the test database's port and password.

## Images

| Image | Built from | Build args | Used by |
| --- | --- | --- | --- |
| `mrr-backend-web` | `backend/Dockerfile`, context `./backend` | `UV_EXTRAS="--extra docs"`, `GIT_SHA` | `api`, `summarize-worker` |
| `mrr-backend-classifier` | `backend/Dockerfile`, context `./backend` | `UV_EXTRAS="--extra docs --extra classifier"`, `GIT_SHA` | `segment-worker` |
| `mrr-frontend` | `frontend/Dockerfile`, context `./frontend` | None | `web` |
| `mrr-docs` | `docs-site/Dockerfile`, context `.` | None | `docs` |
| `postgres:16` | Pulled | - | `postgres` (both stacks) |
| `redis:7` | Pulled | - | `redis` (both stacks) |
| `nginx:1.30` | Pulled | - | `proxy` |

The two backend images are built from the same Dockerfile and the same code. They differ only in
the optional dependency sets installed, which is why `docker compose build api` does not update
`segment-worker`.

### Backend image (`backend/Dockerfile`)

| Item | Value |
| --- | --- |
| Base | `python:3.12-slim` |
| Package manager | `uv` 0.11.2, copied from `ghcr.io/astral-sh/uv:0.11.2` |
| System packages | `tesseract-ocr`, `poppler-utils` |
| Dependency install | `uv sync --locked --no-dev ${UV_EXTRAS}` from `pyproject.toml` and `uv.lock` |
| Embedding model | `all-MiniLM-L6-v2` downloaded at build time, only when `UV_EXTRAS` contains `classifier` |
| Environment | `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HOME=/opt/hf-cache`, `PATH=/app/.venv/bin:$PATH`, `PYTHONPATH=/app`, `BUILD_SHA=$GIT_SHA` |
| Build args | `UV_EXTRAS` (default `--extra docs`), `GIT_SHA` (default `unknown`, declared last so a new commit does not invalidate the dependency layers) |
| Working directory | `/app` (the contents of `backend/`, minus the paths in `backend/.dockerignore`, which include `tests`, `.env` and `uploads`) |
| Exposed port | 8000 |
| Default command | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |

### Frontend image (`frontend/Dockerfile`)

| Item | Value |
| --- | --- |
| Stages | `build` then `runtime`, both `node:22-slim` |
| Build stage | `corepack enable`, `pnpm install --frozen-lockfile`, `pnpm build` |
| Runtime contents | `.next/standalone`, `.next/static`, `public/` |
| Environment | `NODE_ENV=production`, `HOSTNAME=0.0.0.0`, `PORT=3000` |
| Exposed port | 3000 |
| Default command | `node server.js` |

### Docs image (`docs-site/Dockerfile`)

| Item | Value |
| --- | --- |
| Stages | Build on `python:3.12-slim` with `uv` 0.11.2, then serve on `nginx:1.30` |
| Build stage | `uv sync --frozen`, then `mkdocs build --strict` of the `docs/` folder |
| Runtime contents | The built site under `/usr/share/nginx/html/docs` |
| Build context filter | `docs-site/Dockerfile.dockerignore` (the root `.dockerignore` excludes `docs` and `*.md`) |
| Container port | 80 |

## Volumes

| Volume (in the file) | Docker volume name | Stack | Mounted by | Holds |
| --- | --- | --- | --- | --- |
| `mrr_pgdata` | `mrr_mrr_pgdata` | App | `postgres` at `/var/lib/postgresql/data` | The application database |
| `mrr_uploads` | `mrr_mrr_uploads` | App | `api`, `segment-worker`, `summarize-worker` at `/app/uploads` | Uploaded PDFs (`<user id>/<document id>.pdf`) and prepared exports (`<user id>/downloads/<token>`) |
| `mrr_pgdata` | `<project>_mrr_pgdata`, for example `mrrtest_mrr_pgdata` | Test | `postgres` at `/var/lib/postgresql/data` | The throwaway test database |

Compose prefixes each named volume with the project name, so the two `mrr_pgdata` volumes are
different volumes as long as the two stacks run under different project names.

## Bind mounts

| Host path | Container path | Mode | Service | Purpose |
| --- | --- | --- | --- | --- |
| `./instance` | `/app/instance` | read-only | `api` | The legacy Flask SQLite database, read by `backend/scripts/migrate_from_sqlite.py` |
| `./secrets` | `/secrets` | read-only | `api`, `segment-worker`, `summarize-worker` | The Vertex service-account key named by `GOOGLE_APPLICATION_CREDENTIALS` |
| `./deploy/nginx.conf` | `/etc/nginx/conf.d/default.conf` | read-only | `proxy` | The proxy configuration |

## Ports

| Port | Where | Owner |
| --- | --- | --- |
| 8080 | Host | `proxy` (container port 80): the only entry point to the app |
| 5433 | Host | App stack `postgres` (container port 5432) |
| 5432 | Host | Test stack `postgres` |
| 6379 | Host | Test stack `redis`. The app stack's `redis` publishes no host port. |
| 8000 | Container network | `api` |
| 3000 | Container network | `web` |
| 80 | Container network | `docs`, and `proxy` inside its container |

## Proxy routes (`deploy/nginx.conf`)

One `server` block listening on port 80 with `server_name _`. Upstreams are resolved per request
through Docker's DNS (`resolver 127.0.0.11 valid=10s ipv6=off`) by assigning the upstream to a
variable before `proxy_pass`, so a recreated container's new address is picked up without
restarting the proxy.

| Location | Upstream | Settings |
| --- | --- | --- |
| `~ ^/api/documents/[^/]+/downloads/[^/]+$` | `api:8000` | `proxy_buffering off`; `proxy_read_timeout 300s`; access log in the `mrr_download` format |
| `~ ^/api/documents/[^/]+/downloads/[^/]+/status$` | `api:8000` | `access_log off` |
| `/api/` | `api:8000` | `proxy_read_timeout 300s` |
| `= /docs` | - | `return 301 /docs/` |
| `/docs/` | `docs:80` | - |
| `/` | `web:3000` | nginx defaults |

Every proxied location sets `Host`, `X-Real-IP`, `X-Forwarded-For` and `X-Forwarded-Proto`. The
two regex locations take precedence over the `/api/` prefix match.

Server-wide settings:

| Directive | Value |
| --- | --- |
| `listen` | `80` |
| `client_max_body_size` | `500m` |
| `client_body_timeout` | `300s` |
| `resolver` | `127.0.0.11 valid=10s ipv6=off` |

### Download log format `mrr_download`

```text
download status=$status document=$mrr_dl_document token=$mrr_dl_token8 sent=$body_bytes_sent of=$sent_http_content_length completed=$request_completion time=$request_time
```

| Field | Meaning |
| --- | --- |
| `status` | HTTP status sent to the browser |
| `document` | The document id from the URL (`map` on `$uri`; `-` when it does not match) |
| `token` | The first 8 characters of the download token (`map` on `$uri`; `-` when it does not match) |
| `sent` | Body bytes written to the browser |
| `of` | The `Content-Length` the API declared |
| `completed` | `OK` when the request completed, empty when the browser left first |
| `time` | Request time in seconds |

Written to `/var/log/nginx/access.log` for the download location only. The line replaces the standard access-log line for download URLs, so the access log never
holds a full download link. [Exports and downloads](../explanation/exports-and-downloads.md)
explains the download flow.

## Related pages

- [How to run the app locally](../how-to/run-the-app-locally.md)
- [How to deploy to the server](../how-to/deploy-to-the-server.md)
- [How to back up and restore](../how-to/back-up-and-restore.md)
- [Configuration](../reference/configuration.md)
- [Architecture](../explanation/architecture.md)
