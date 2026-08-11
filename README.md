# AI Medical Record Review (MRR AI)

Turns a large scanned medical-record PDF (200-2600 pages) into a summarized Medical Record Review.
A reviewer corrects the machine's work at every stage - the app is an assistant, not an autopilot.

> **PHI / HIPAA.** This app processes real patient medical records and sends content to a
> BAA-covered Vertex endpoint. Never commit patient data: not PDFs, not OCR text, not page maps, not
> Word deliverables - their **filenames alone carry patient surnames**. Sample and labelled data live
> outside the repo. Stricter PHI review is required on every PR.

## Pipeline

1. **Segment** the PDF into sub-documents (page ranges) - Gemini vision over overlapping windows.
2. **Categorize** each sub-document - a rules -> embeddings -> Gemini-enum cascade.
3. **Review & correct** - the reviewer fixes boundaries, categories and metadata, and chooses which
   sub-documents to summarize.
4. **Duplicate check** - started by the reviewer, over the rows they selected.
5. **Summarize** each selected sub-document with a category-specific prompt.
6. **Export** the assembled review.

Stages pass state through Postgres rows, not files. Jobs run on Redis/RQ workers.

## Layout

| directory      | what                                                                                          |
| -------------- | --------------------------------------------------------------------------------------------- |
| `backend/`     | FastAPI app, SQLAlchemy + Alembic, RQ workers, prompts, tests                                 |
| `frontend/`    | Next.js app; the review workbench is `/records/[id]`                                          |
| `docs/`        | documentation - start at [`docs/INDEX.md`](docs/INDEX.md)                                     |
| `experiments/` | segmentation research; `a1-segmentation/EXPERIMENT-LOG.md` is worth reading                   |
| `legacy/`      | **the pre-rewrite Flask app. Nothing here runs** - see [`legacy/README.md`](legacy/README.md) |

New here? Read [`CLAUDE.md`](CLAUDE.md) - it is written for AI assistants but it is the fastest
orientation for a human too, and it lists the traps.

## Run it

```bash
cp .env.example .env      # then fill it in
docker compose up -d      # http://localhost:8080
docker compose exec api alembic upgrade head   # first run, empty database
```

Vertex is the BAA-covered AI path and is required in production. A service-account key can be
mounted at `secrets/vertex-sa.json`. `SECRET_KEY` and `SECURITY_PASSWORD_SALT` are required
(sessions + password hashing) - generate each once per machine and keep them stable: rotating
`SECRET_KEY` logs everyone out, and rotating the salt invalidates every stored password.

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Develop

```bash
cd backend && uv sync
uv run ruff check . && uv run ruff format .
uv run pyright                      # advisory
```

```bash
cd frontend && pnpm install
pnpm test        # vitest
pnpm typecheck
```

### Tests

The suite runs against a **separate throwaway database**, not the app's:

```bash
docker compose -f docker-compose.dev.yml up -d postgres   # test DB, port 5432
cd backend && uv run alembic upgrade head && uv run pytest -q
```

Do **not** set `DATABASE_URL` to point at the app database (port 5433). The fixtures insert and
delete rows; `backend/tests/conftest.py` refuses and explains why.

### Things that catch people out

- **The images are baked, not bind-mounted.** Editing `backend/` or `frontend/` does nothing to a
  running container until you rebuild and recreate it.
- **`docker compose up -d web` will report "Running" and not pick up a new image.** Use
  `--force-recreate`, or a frontend change ships as a silent no-op.
- **Seeding is one-shot.** `seed_catalog()` returns early once any category row exists, so editing a
  seed constant changes nothing on an existing database - carry it in a migration instead.
- **A 429 from Vertex is Dynamic Shared Quota**, not an exhausted allowance. Retry; there is nothing
  to wait for.

Pre-commit runs gitleaks, detect-private-key and a large-file check to keep secrets and patient PDFs
out of git. CI runs backend tests, frontend tests, e2e, secret scanning and SonarCloud on every PR.

## Deploy

See [`docs/RUNBOOK.md`](docs/RUNBOOK.md). Back up the database before running migrations; the runbook
has the exact commands.

## Status

Live and deployed. Recent work: per-call model tiering with prompt provenance, an additive-increase
pacer for Vertex admission, date-first duplicate clustering, duplicate detection gated behind review
and scoped to selected rows, deposition summaries in three-page groups with transcript page
citations, a single injury-date read at segmentation, and a page-text store so each page is OCR'd
once.
