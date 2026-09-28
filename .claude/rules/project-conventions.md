# Project Conventions (MRR AI)

Load-bearing conventions for any change in this repo. `CLAUDE.md` at the root carries the
repo-wide rules and traps; each folder's `CLAUDE.md` carries that area's. This file holds the
design conventions that cut across folders.

- **Layering.** Routes are `APIRouter`s in `backend/app/api/`, included in `backend/app/main.py`.
  Pipeline logic lives in `backend/app/services/` and never imports FastAPI; services take an
  explicit SQLAlchemy `Session` where they need one. RQ job functions and queue routing are
  `backend/app/worker/`. The frontend is Next.js App Router in `frontend/`; the workbench is
  `frontend/components/review/`.

- **State is Postgres and Redis, never process memory.** The app is multi-process by design:
  `api`, `segment-worker` (3) and `summarize-worker` (3) are separate containers, built from two
  images (`mrr-backend-web` for `api` and `summarize-worker`, `mrr-backend-classifier` for
  `segment-worker`). Nothing may rely on in-process state surviving between requests or stages.

- **Stages talk through rows.** Segmentation writes `segment_rows` (the machine's output, kept)
  and `review_rows` (the reviewer's editable copy); summaries go to `summaries`. No file is passed
  between stages, and no live code reads or writes the old page-map CSV.

- **Thread safety is real.** Stages fan out over `ThreadPoolExecutor`s. A SQLAlchemy `Session` is
  not thread-safe: resolve database reads before entering a pool (as `worker/tasks.py` resolves
  prompts per category up front) or open a short-lived session inside the worker thread (as
  `services/classification.py` does). A module-level cache a pool touches needs a lock.

- **Model calls cross one seam.** Every call goes through `backend/app/services/llm/`, which picks
  the backend (`gemini`, `openai` or `vllm`) per stage from `LLM_BACKEND` and
  `LLM_BACKEND_OVERRIDES`; the default is `gemini`. With `ENVIRONMENT=prod` the app refuses to
  boot unless Gemini traffic goes to Vertex (`GOOGLE_GENAI_USE_VERTEXAI=true`), OpenAI has its
  zero-data-retention acknowledgement, and vLLM points at an approved origin. A change to any AI
  path needs the PR template's HIPAA section filled in.

- **Measurement over opinion.** Segmentation recall and summary quality are measured, not argued.
  A document missed at segmentation is never summarized and nothing downstream surfaces it, so a
  segmentation prompt or schema change needs a number. Read
  `experiments/a1-segmentation/EXPERIMENT-LOG.md` first; the harnesses are in
  `backend/scripts/eval/`. Prompt provenance is a fingerprint computed from the prompt text, so a
  prompt change is recorded without anyone bumping a version constant.

- **Prompt resolution.** `catalog.get_prompt` resolves: the category's database row, then its code
  prompt in `services/prompts.py`, then the General (100) row, then the General code prompt. So a
  `prompts.py` edit reaches every category without an admin-edited row. The category prompt is only
  part of the system message: `summarize_engine` prepends shared rule blocks chosen by category.

- **Secrets** come from `.env` (never committed; templates `deploy/env.docker.example` and
  `.env.example`) and fail fast at startup. Service-account keys go in `secrets/`, which git
  ignores apart from `.gitkeep`.

- **Tooling.** Backend: uv, Python 3.12, `uv sync --extra docs`; ruff for lint and format (both CI
  gates). Frontend: pnpm, `pnpm typecheck`, `pnpm test` (vitest), `pnpm e2e` (Playwright). Do not
  run prettier: the repo has no prettier config and running it reformats hundreds of lines.

- **Tests and gates.** Synthetic data only; mock the model providers and OCR. CI enforces coverage
  floors (backend 90% branch-aware, frontend 80% on each of four metrics) in the `coverage-floor`
  job, and SonarCloud's quality gate plus a zero-new-issues check in the `sonarcloud` job. Both are
  required checks on `main`. Details: `docs/reference/ci-and-merge-gates.md`.

- **Workflow.** Pull requests only, squash-merged; commit messages and PR titles use the scopes in
  `.claude/rules/commit-scopes.md`. Docs change in the same PR as the code they describe; the
  guards that check it are in `docs/how-to/work-on-these-docs.md` ("How the docs stay current").
