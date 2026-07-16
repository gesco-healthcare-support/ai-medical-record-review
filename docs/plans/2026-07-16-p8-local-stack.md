# P8 (local) - full stack in containers + SQLite -> Postgres migration

status: in-progress
branch: feat/nextjs-fastapi-rewrite
approach: code (infra; verify-after)

## Goal

Run the ENTIRE re-platformed app end-to-end on the local machine as the deployable stack - the
same artifact that later runs on the in-house LAN server (no cloud). One `docker compose up`
brings up Postgres + Redis + FastAPI + Next.js + both RQ workers + an nginx reverse proxy, with
the legacy Flask SQLite migrated into Postgres. Everything except pushing to the server box.

The one external dependency is Vertex AI (LLM/VLM inference); all app/data/queue services are local.

## What was added

- `backend/Dockerfile` - API + workers image. Tesseract + Poppler baked in. `UV_EXTRAS` build arg:
  `--extra docs` (API, summarize worker; torch-free) vs `--extra docs --extra classifier`
  (segment worker; torch/embeddings).
- `frontend/Dockerfile` - Next.js standalone build (`output: "standalone"` added to next.config.ts).
- `docker-compose.yml` (root) - the full stack. Postgres on host 5433 (dev stack keeps 5432);
  Redis persistence off (HIPAA); shared `mrr_uploads` volume across api + workers; `./secrets`
  mounted for a Vertex SA key; `./instance` mounted read-only on api for the one-time migration.
- `deploy/nginx.conf` - same-origin proxy: `/api` -> api:8000, everything else -> web:3000.
- `deploy/env.docker.example` - env template (copy to `.env`). `.env*` is git-ignored + blocked
  by the protect-secrets hook, so the template lives under deploy/ without the `.env` prefix.
- `secrets/.gitkeep` + `.gitignore` (secrets/) - runtime SA key location, never committed.

## Design decisions

- ONE image per tier, split by torch: API + summarize worker stay light; only the segment worker
  carries torch. (Could split further later; fine for a LAN box.)
- `ENVIRONMENT=dev` locally so the session cookie is NOT Secure and login works over plain http;
  the server sets `ENVIRONMENT=prod` + TLS on the proxy (cookie becomes Secure).
- Postgres gets its OWN fresh volume + a fresh migration (mirrors what the server will do), leaving
  the dev Postgres (docker-compose.dev.yml) untouched.
- nginx (matches the Patient Portal / vidcon in-house pattern).

## Setup / runbook

    cp deploy/env.docker.example .env
    # edit .env: set SECRET_KEY (openssl rand -hex 32) and SECURITY_PASSWORD_SALT (the REAL Flask
    # salt, so migrated logins verify) + GOOGLE_CLOUD_PROJECT; optionally drop a Vertex SA key at
    # ./secrets/vertex-sa.json

    docker compose build
    docker compose up -d postgres redis
    docker compose run --rm api alembic upgrade head                                 # schema
    docker compose run --rm api python scripts/migrate_from_sqlite.py --sqlite instance/mrr.db  # one-time data
    # (relative --sqlite path avoids Git Bash mangling /app...; the source opens read-only)
    docker compose up -d                                                              # whole app
    # open http://localhost:8080

Rebuild after code changes: `docker compose build <service> && docker compose up -d <service>`.

## What Adrian must provide

- The REAL `SECURITY_PASSWORD_SALT` (from the legacy mrr_ai `.env`) to test a migrated login.
  New accounts work with any salt. (Already present in the repo-root `.env`, which compose reads.)
- Vertex credentials in the containers, to run the AI jobs (identify / summarize / bundle-summarize
  / reprocess). Two options:
  - Service-account key (best for the server): drop it at `./secrets/vertex-sa.json` and set
    `GOOGLE_APPLICATION_CREDENTIALS=/secrets/vertex-sa.json` in `.env`.
  - gcloud USER ADC (fine for a local demo; token reauths over time): copy the host ADC file into
    the mounted secrets dir and point at it:
        cp "$APPDATA/gcloud/application_default_credentials.json" secrets/adc.json
    then `GOOGLE_APPLICATION_CREDENTIALS=/secrets/adc.json` in `.env`.
- `GOOGLE_CLOUD_LOCATION=us-central1` (NOT `global`): the gen-lang-client project has NO Vertex
  quota in `global` (see [[mrr-ai-vertex-auth-quota]]) - every AI call 429s otherwise.

Without Vertex creds the app runs and every non-AI path works; AI jobs fail at runtime only.

## Migrated document files (making migrated records viewable/processable)

The DB migration copies document METADATA (stored_path pointed at the legacy host path
`...\uploads\<user_id>\<doc_id>.pdf`). To make migrated records open, copy the legacy PDFs into
the uploads volume and rewrite stored_path to the container path:

    MSYS_NO_PATHCONV=1 docker compose cp ./uploads/1 api:/app/uploads/
    docker compose exec -T postgres psql -U mrr -d mrr -c \
      "UPDATE documents SET stored_path = '/app/uploads/' || user_id || '/' || id || '.pdf' \
       WHERE stored_path NOT LIKE '/app/%';"

Adjust the source dir (`./uploads/1`) to wherever the legacy PDFs live. On the SERVER, copy the
Flask upload tree into the volume the same way, then run the same UPDATE.

## When cutting to the server (P8 proper, later)

Same compose, `.env` with `ENVIRONMENT=prod` + real secrets, TLS on the proxy (nginx 443 + certs),
run the migration once against the server Postgres, provision disk encryption + Postgres backups,
cap worker replicas to the Vertex quota, parallel-run beside Flask, then point the LAN proxy at
this stack. main stays Flask until then.

## Verify (local, no AI)

`docker compose build` succeeds; stack boots; migration row counts match the SQLite; register +
login a NEW account over http://localhost:8080; upload a PDF; review editor renders; admin loads;
bundle "Download combined PDF" works. AI paths = Adrian, with ADC.
