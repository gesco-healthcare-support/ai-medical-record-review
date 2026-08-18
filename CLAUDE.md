# CLAUDE.md - AI Medical Record Review (MRR AI)

Project memory for Claude Code. The global rules in `~/.claude/` apply; this file adds
project-specific context.

## What this is

Turns a large scanned medical-record PDF (200-2600 pages) into a summarized Medical Record Review.
A reviewer corrects the machine's work at every stage - the app is an assistant, not an autopilot.

**Pipeline:** upload -> **segment** (find sub-document boundaries) -> **categorize** ->
reviewer corrects -> **duplicate check** -> **summarize** -> export.

- **Frontend:** `frontend/` - Next.js (App Router), pnpm, vitest. The review workbench is
  `/records/[id]`.
- **Backend:** `backend/` - FastAPI (uvicorn), SQLAlchemy + Alembic, Postgres, Redis + RQ workers,
  uv for dependencies.
- **AI:** google-genai against **Vertex** (the BAA-covered path). Segmentation and classification on
  flash tiers; summary bodies on a pro tier. OpenAI exists behind a config flag and is not the
  default - see `backend/app/services/llm/`.
- **OCR:** pytesseract + Poppler, in the backend image.
- **Gates:** ruff (lint + format), pyright (advisory), pre-commit + gitleaks, GitHub Actions CI,
  SonarCloud.

## Read this before you change anything

**`legacy/` is the pre-rewrite Flask app and nothing in it runs.** It was moved there on 2026-08-11
because both humans and AI assistants kept reading it as the current system. It still contains four
`CLAUDE.md` files describing that old app, and a stale copy of the segmentation prompt. See
`legacy/README.md`.

If a doc, comment or `CLAUDE.md` mentions Flask, port 5010, `app.py`, blueprints, templates, or a
page-map CSV passed between stages, it is describing the legacy app.

## Where things live

| what                     | where                                                                                     |
| ------------------------ | ----------------------------------------------------------------------------------------- |
| FastAPI routers          | `backend/app/api/`                                                                        |
| Pipeline stages          | `backend/app/services/` (`segment_engine`, `classification`, `dedup`, `summarize_engine`) |
| RQ tasks + queue routing | `backend/app/worker/`                                                                     |
| Prompts                  | `backend/app/services/prompts.py` (per category) + `gemini.py` (segmentation)             |
| DB models / migrations   | `backend/app/models.py`, `backend/alembic/versions/`                                      |
| Backend tests            | `backend/tests/`                                                                          |
| Review workbench UI      | `frontend/components/review/`                                                             |
| Segmentation research    | `experiments/a1-segmentation/`                                                            |

## Things that will bite you

- **Two databases.** `docker-compose.yml` = the APP on host port **5433** (real data).
  `docker-compose.dev.yml` = the throwaway TEST database on **5432**. Run the app against the first
  and the suite against the second. `backend/tests/conftest.py` refuses to run against the app
  database and will tell you so; do not set `DATABASE_URL` to get around it.
- **The backend image is baked, not bind-mounted.** Editing `backend/` does nothing to a running
  container until you build and recreate. The frontend is the same.
- **There are TWO backend images, and `build api` only rebuilds one.** `api` and `summarize-worker`
  run `mrr-backend-web` (`--extra docs`); `segment-worker` runs `mrr-backend-classifier`
  (`--extra docs --extra classifier`, the torch one). So `docker compose build api` leaves the
  segment worker on whatever it had, and `--force-recreate` restarts it from that stale image
  without complaint. Anything the segment worker owns - `classification.py`, `segment_engine.py`,
  `windows.py` - then appears not to work: measured 2026-08-17, `match_rules` returned the fixed
  answer in `api` and the old one in `segment-worker`, same call, because the classifier image was
  five days old. Name every service you changed.
- **A fresh test database needs `alembic upgrade head`** before the suite will run, or every
  DB-touching test errors with `relation "user" does not exist`.
- **Seeding is one-shot.** `seed_catalog()` returns early once any `Category` row exists, so editing
  a seed constant changes nothing on a database that already exists. Carry such changes in a
  migration - see `a9c4e13f70b2` and `f1a83b5c60d2` for the pattern and the trap.
- **A 429 from Vertex is Dynamic Shared Quota**, not an exhausted allowance. It means capacity was
  unavailable at that moment; retry rather than wait for a reset.
- **Segmentation recall is the metric that matters most.** A document missed at segmentation is never
  summarized and nothing downstream surfaces it. Over-segmentation is visible to the reviewer and
  fixable; under-segmentation is not. Treat any change to the segmentation prompt or schema as
  requiring a measurement, not an opinion.

## PHI / HIPAA (strict)

- Real patient records are PHI. **Never** commit PDFs, OCR text, page-map CSVs, patient names, or
  Word deliverables - their filenames alone carry surnames, which is why `.gitignore` blocks
  `*.doc`/`*.docx` outside `docs/reference/`.
- Sample and labelled data live outside the repo on `P:`; `uploads/` and experiment caches are
  gitignored.
- gitleaks + detect-private-key + large-file pre-commit hooks guard every commit.
- Secrets via `.env` (never committed; see `.env.example`).
- Vertex is the BAA-covered path and is required in production. OpenAI additionally requires Zero
  Data Retention acknowledged on the org before any PHI may go near it.

## Commands

```bash
docker compose up -d                                   # the app -> http://localhost:8080
docker compose -f docker-compose.dev.yml up -d postgres # the TEST database (port 5432)
```

```bash
cd backend && uv sync
uv run alembic upgrade head
uv run pytest -q                                       # do NOT export DATABASE_URL
uv run ruff check . && uv run ruff format .
```

```bash
cd frontend && pnpm install
pnpm test          # vitest
pnpm typecheck
```

After changing backend code, rebuild before expecting a container to see it:

```bash
# Build BOTH backend images. `build api` alone rebuilds mrr-backend-web only, so the segment
# worker (mrr-backend-classifier) keeps running old code and the change silently does not apply.
docker compose build api segment-worker summarize-worker
docker compose up -d --force-recreate api segment-worker summarize-worker
```

Only touched a service on `mrr-backend-web` (api, summarize-worker)? `docker compose build api` is
enough. Touched anything the segment worker runs - categorization, segmentation, windowing - and you
need `build segment-worker` too, or you are testing the previous image.

## Key references

- `docs/INDEX.md` - the documentation map (Diataxis: explanation / how-to / reference).
- `docs/RUNBOOK.md` - run and deploy.
- `docs/decisions/` - ADRs. These are HISTORICAL records; several describe the Flask app and are
  correct as history. Do not rewrite them to match the present.
- `.claude/rules/commit-scopes.md` - the allowed commit/PR scopes. Source of truth; add a scope there
  in the PR that needs it.
- `docs/reference/Categories ...docx` - the category taxonomy.
- `experiments/a1-segmentation/EXPERIMENT-LOG.md` - what has been tried on segmentation and what
  failed. Read before proposing a segmentation approach; several obvious ideas are already measured
  and rejected there.

## Status

The Next.js + FastAPI rewrite is live and deployed. Recent work: per-call model tiering and prompt
provenance, an additive-increase pacer for Vertex admission, date-first duplicate clustering,
duplicate detection gated behind the review phase and scoped to included rows, deposition summaries
in three-page groups with transcript page citations, one injury-date read at segmentation, and a
page-text store so each page is OCR'd once.

Known gaps: `docs/research/` and some `docs/prompts/` files are historical and describe the legacy
pipeline; the segmentation experiment harness only recently started importing the live prompt rather
than a legacy copy.
