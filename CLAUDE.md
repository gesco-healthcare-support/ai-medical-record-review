# CLAUDE.md - MRR AI (AI Medical Record Review)

Instructions for AI coding agents working anywhere in this repository. Each folder has its own
`CLAUDE.md` with the rules for that area; Claude Code loads it when you read files there.

## What this is

A FastAPI + RQ/Redis + Postgres backend (`backend/`) and a Next.js frontend (`frontend/`) that turn
a scanned medical-record PDF into a reviewed, summarized Medical Record Review. Pipeline: upload ->
identify (page text/OCR, segment, categorize, boundary verify, injury dates) -> reviewer corrects
-> duplicate check -> summarize (draft + audit) -> export.

Full documentation: `docs/` (site built by `docs-site/`). Start at `docs/explanation/architecture.md`;
terms are in `docs/reference/glossary.md`.

## Non-negotiable rules

- **Patient data.** Real medical records. Never commit PDFs, OCR text, exports, `.doc`/`.docx`
  deliverables or anything derived from a real record; never log a filename, patient field, OCR
  text, prompt or model response. Tests and examples use synthetic data only. Never open
  `Record Reviews/`, `uploads/`, `instance/`, `secrets/` or a real `.env`.
- **The repository is public.** No hostnames, IP addresses, server account names, emails, people's
  names or secrets in any committed file. Use placeholders (`<SERVER_HOST>`).
- **Model providers are a compliance boundary.** Every model call goes through the provider seam in
  `backend/app/services/llm/`; never call a vendor SDK directly from app code. Do not weaken the
  boot guards in `backend/app/config.py` (Vertex required in production, OpenAI zero-data-retention
  acknowledgement, vLLM origin allowlist).
- **Pull requests only.** `main` is protected: changes arrive by squash-merged PR with all required
  checks green. Never push to `main`.
- **Commit and PR titles:** `<type>(<scope>): <subject>`, imperative, ASCII, <= 72 chars, scope from
  `.claude/rules/commit-scopes.md` (add a scope there in the PR that needs it).
- **Docs change with the code.** A change to behaviour, a route, a setting, a table, a migration, a
  compose service or a frontend route updates the page that describes it in the same PR.
  `backend/tests/test_docs_reference_drift.py` fails when a reference page falls behind, and
  `backend/tests/test_docs_guides.py` when a README misses a file or a doc cites a path or symbol
  that no longer exists. Conventions: `docs/how-to/work-on-these-docs.md`.
- **Before you finish, re-read the docs your change touches.** The Stop hook
  (`.claude/hooks/docs-reminder.sh`) names the folder guides and pages to check. Update what the
  change made wrong. If they are still right, run the `--reviewed <folder>` command it prints. Keep
  each folder's README file list complete; keep CLAUDE.md to rules, traps and commands.
- **Working files stay out of git.** `docs/plans/` and `docs/backlog.md` are gitignored on purpose;
  never stage anything in them. Never `git add -A`; stage by explicit path.

## Traps that apply everywhere

- **Two databases.** `docker-compose.yml` is the app (Postgres on host port **5433**, real data).
  `docker-compose.dev.yml` is the throwaway test database (**5432**) and Redis (6379). The backend
  suite refuses to run against the app database - do not set `DATABASE_URL` to get around it.
- **Images are baked, not bind-mounted.** Editing code changes nothing in a running container
  until you rebuild and `--force-recreate`. There are TWO backend images: `api` and
  `summarize-worker` run `mrr-backend-web`; `segment-worker` runs `mrr-backend-classifier` (torch).
  `docker compose build api` does NOT rebuild the segment worker. Name every service you changed.
- **A setting reaches a container only if `docker-compose.yml` names it.** Setting it in `.env`
  is not enough. Most settings are deliberately not passed; see
  `docs/explanation/configuration-model.md` before adding one.
- **The category catalog is DB-first.** Once any `categories` row exists, `taxonomy.py` edits do not
  reach that database; carry them in a guarded migration. See
  `docs/how-to/add-or-change-a-category.md`.
- **The retired Flask app was removed on 2026-09-30** (it is in git history). Work in `backend/`.
  `legacy/docs/` keeps its documents only; they do not describe the current system.
- **Segmentation recall matters most.** A sub-document missed at segmentation is never summarized
  and nothing downstream surfaces it. Treat any segmentation prompt or schema change as needing a
  measurement; read `experiments/a1-segmentation/EXPERIMENT-LOG.md` first.

## Commands

```bash
# Backend (from backend/): deps, lint + format + type gates, tests against the TEST stack
docker compose -p mrrtest -f ../docker-compose.dev.yml up -d --wait postgres redis
uv sync --extra docs                # bare `uv sync` omits required deps; --extra classifier = torch
DATABASE_URL=postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr SECRET_KEY=dev-only-secret SECURITY_PASSWORD_SALT=dev-only-salt uv run alembic upgrade head
uv run ruff check . && uv run ruff format --check . && uvx pyright==1.1.414
uv run pytest -q                    # do NOT export DATABASE_URL; conftest finds the test DB
```

```bash
# Frontend (from frontend/)
corepack enable && pnpm install
pnpm lint && pnpm typecheck && pnpm test   # vitest; `pnpm e2e` needs the app stack on :8080
```

```bash
# App stack (from the repo root) and the docs site
docker compose build && docker compose up -d --wait postgres redis
docker compose run --rm api alembic upgrade head
docker compose up -d                # http://localhost:8080 ; docs at /docs/
cd docs-site && uv run --frozen mkdocs build --strict
```

Details and the CI gates: `docs/how-to/run-the-tests.md`, `docs/reference/ci-and-merge-gates.md`.

## Where things live

| area | code | agent rules |
| --- | --- | --- |
| HTTP API and auth | `backend/app/api/`, `backend/app/auth/`, `backend/app/schemas/` | the `CLAUDE.md` in each |
| Pipeline services | `backend/app/services/` | `backend/app/services/CLAUDE.md` |
| Model providers | `backend/app/services/llm/` | `backend/app/services/llm/CLAUDE.md` |
| Jobs and workers | `backend/app/worker/` | `backend/app/worker/CLAUDE.md` |
| Settings | `backend/app/config.py` | `backend/app/CLAUDE.md` |
| Models and migrations | `backend/app/models.py`, `backend/alembic/` | `backend/alembic/CLAUDE.md` |
| Scripts | `backend/scripts/` | `backend/scripts/CLAUDE.md` |
| Backend tests | `backend/tests/` | `backend/tests/CLAUDE.md` |
| Frontend | `frontend/app/`, `components/`, `hooks/`, `lib/` | `frontend/CLAUDE.md` and each folder's |
| Proxy and server bootstrap | `deploy/` | `deploy/CLAUDE.md` |
| Docs site | `docs/`, `docs-site/` | `docs-site/CLAUDE.md` |
