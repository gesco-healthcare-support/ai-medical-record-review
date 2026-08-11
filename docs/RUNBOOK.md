# Runbook

How to run the app locally, and how to deploy it.

> Replace `<SERVER_HOST>` / `<SERVER_USER>` with the real values from your internal notes or secret
> store. Do NOT commit real hosts or credentials.

## What runs

Two containers serve the app, plus workers:

| service                                    | what                             |
| ------------------------------------------ | -------------------------------- |
| `web`                                      | Next.js frontend                 |
| `api`                                      | FastAPI (uvicorn)                |
| `segment-worker` x3, `summarize-worker` x3 | RQ workers                       |
| `postgres`, `redis`                        | state and queue                  |
| `proxy`                                    | fronts web + api on **one port** |

The app is reached on **port 8080**, not on the frontend or API port directly.

## Run locally

```bash
cd /path/to/mrr-ai
docker compose up -d
```

Then open <http://localhost:8080/>.

Secrets go in `.env` (copy `.env.example`). The app fails fast at startup if required ones are
missing. Vertex is the BAA-covered AI path and is required in production; a service-account key can
be mounted at `secrets/vertex-sa.json`.

First run on an empty database also needs the schema:

```bash
docker compose exec api alembic upgrade head
```

### The other database

`docker-compose.dev.yml` starts a **separate, throwaway Postgres on port 5432** for the test suite.
It is not the app's database (the app's is 5433) and it holds no real data.

```bash
docker compose -f docker-compose.dev.yml up -d postgres
cd backend && uv run alembic upgrade head && uv run pytest -q
```

Do not point the test suite at the app database. `backend/tests/conftest.py` refuses, because the
fixtures insert and delete rows.

## Deploy

The server is a plain git checkout at `/home/<SERVER_USER>/mrr` running the same compose stack.
**The backend and frontend images are baked, so a `git pull` alone changes nothing** - they must be
rebuilt.

```bash
ssh <SERVER_USER>@<SERVER_HOST>
cd /home/<SERVER_USER>/mrr
```

Back up before any migration:

```bash
BK=/home/<SERVER_USER>/mrr-backup-$(date +%Y%m%d-%H%M%S); mkdir -p $BK
docker compose exec -T postgres pg_dump -U mrr -Fc mrr > $BK/mrr.dump
git rev-parse HEAD > $BK/git-sha.txt
```

Then:

```bash
git pull --ff-only
docker compose build api                 # and `web` if frontend/ changed
docker compose up -d --force-recreate api segment-worker summarize-worker
docker compose exec -T api alembic upgrade head
docker compose up -d --force-recreate web   # only if frontend/ changed
```

Verify:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/
docker compose logs api --since 2m | grep -icE 'error|traceback'
docker compose exec -T postgres psql -U mrr -d mrr -t -c 'SELECT version_num FROM alembic_version;'
```

`docker compose up -d web` on its own will report "Running" and **not** pick up a new image - use
`--force-recreate`. A frontend change shipped without it is a silent no-op.

### Rollback

Restore the dump taken above and check out the recorded SHA:

```bash
docker compose exec -T postgres pg_restore -U mrr -d mrr --clean --if-exists < $BK/mrr.dump
git checkout $(cat $BK/git-sha.txt)
```

Then rebuild and recreate as above.

## Retrieve the generated MRRs

Exports are produced through the app's export UI. For files written to disk on the server, retrieve
over SFTP:

- Host `<SERVER_HOST>` | Port `22` | User `<SERVER_USER>`

Anything retrieved this way is PHI. Keep it off the network and out of the repo.

---

**Looking for the old Flask runbook?** The pre-rewrite app served on port 5010 via
`uv run python app.py`. It now lives in [`legacy/`](../legacy/README.md) and does not run.
