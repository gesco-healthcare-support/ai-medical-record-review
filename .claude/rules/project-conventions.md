# Project Conventions (MRR AI)

Load-bearing, project-specific rules. The global `~/.claude` rules also apply, and `CLAUDE.md`
carries the traps; this file is the conventions an assistant must not violate.

Rewritten 2026-08-12. The previous version described the pre-rewrite Flask app - `create_app()` in
`mrr_ai/`, blueprints, `state.py` globals, a single-process constraint and the CSV as the stage
interface. All of that became wrong at #92, and one rule was actively dangerous: it forbade adding a
multi-worker server, which is now the deployed architecture.

- **Architecture:** FastAPI app in `backend/app/`. Routes are `APIRouter`s under
  `backend/app/api/`, included in `backend/app/main.py`. Pipeline logic lives in
  `backend/app/services/` and **must not import FastAPI** - services take an explicit
  SQLAlchemy `Session` where they need one. RQ tasks and queue routing are
  `backend/app/worker/`. The frontend is Next.js App Router in `frontend/`; the review workbench
  is `frontend/components/review/`. `docs/architecture.md` describes the Flask app and is
  marked **[LEGACY]** - do not follow it.

- **State:** there are **no shared mutable globals**. State is Postgres (via SQLAlchemy) and
  Redis (via RQ). The app is **multi-process by design** - `api`, `segment-worker` and
  `summarize-worker` are separate containers off one image - so nothing may rely on
  in-process state surviving between requests or between stages.

- **Thread safety is a real constraint, not a theoretical one.** Stages fan out over
  `ThreadPoolExecutor` (`PIPELINE_WORKERS`, `CLASSIFY_WORKERS`, `SEGMENT_WINDOW_WORKERS`). A
  SQLAlchemy `Session` is **not** thread-safe, so resolve every DB read *before* entering a
  pool (see `worker/tasks.py` resolving prompts per category up front) or open a short-lived
  session inside the worker (see `services/classification.py`). Module-level caches that a
  pool touches need a lock - `classification.py` holds one for the catalog and another for
  the embedding model.

- **Stage interface: Postgres rows, not a CSV.** Segmentation writes `segment_rows` (the
  immutable model output) and `review_rows` (the reviewer's editable copy). The 6-column
  `start,end,category,doc_date,injury_date,manual_flag` shape still exists, but only as an
  **export/import format** - `docs/reference/csv-contract.md` is marked **[LEGACY]**. Nothing
  internal passes a CSV between stages.

- **PHI (strict).** Never commit PDFs, OCR text, page-map CSVs, patient names, or Word
  deliverables - filenames alone carry surnames, which is why `.gitignore` blocks
  `*.doc`/`*.docx` outside `docs/reference/`. `uploads/`, `secrets/` and experiment caches are
  gitignored. Never log PDF, OCR or LLM bodies. **Vertex is the BAA-covered path and is
  required in production** - `config.py` raises at startup if `ENVIRONMENT=prod` without
  `GOOGLE_GENAI_USE_VERTEXAI=true`. OpenAI exists behind `SUMMARY_PROVIDER=openai` and
  additionally requires a Zero Data Retention acknowledgement in prod; as of 2026-08-11 it is
  out of the project by decision, so do not spend work there. Any change to an AI path needs
  the PR template's HIPAA review section.

- **Measurement over opinion.** Segmentation recall and summarization prompt quality are both
  measured, not argued. A document missed at segmentation is never summarized and nothing
  downstream surfaces it, so treat any segmentation prompt or schema change as requiring a
  number. Read `experiments/a1-segmentation/EXPERIMENT-LOG.md` before proposing a segmentation
  approach - several obvious ideas are already measured and rejected. Prompt fingerprints are
  computed from the prompt text, so **never hand-bump `PROMPT_VERSION`**; provenance moves on
  its own.

- **Prompt resolution has a trap.** `catalog.get_prompt` resolves DB row → **code prompt in
  `services/prompts.py`** → general (100) row → general code prompt. So `prompts.py` is not
  merely a seed: for any category with no `Prompt` row of its own, editing it changes
  delivered output. Whether that holds on a given box is an empirical question - check
  `SELECT role, category_id, length(text) FROM prompts` rather than assuming. A category
  prompt is also only half the system message: `summarize_engine.build_preamble` prepends
  shared rule blocks selected by category id.

- **Secrets:** via `.env`, fail-fast at startup; never hardcoded, never committed. See
  `.env.example`. Service-account keys go in `secrets/` (gitignored, holds only `.gitkeep` in
  git). Rotate anything that has been shared over email or chat.

- **Tooling:** uv (`pyproject.toml` + `uv.lock`), Python 3.12. `uv sync --extra docs` is the
  web/summarize tier and what CI runs - **torch-free, and the right choice for local dev**.
  `--extra classifier` adds sentence-transformers/torch and is the segment worker only. Lint
  and format = ruff (line length 100); types = pyright (advisory while untyped). Pre-commit
  runs ruff, gitleaks, detect-private-key, a 1 MB large-file guard, and whitespace/EOF fixers.
  Frontend: pnpm, `pnpm typecheck`, `pnpm test` (vitest), `pnpm e2e` (Playwright).
  **Do not run prettier** - the repo has no prettier config and is not prettier-formatted, so
  running it reformats hundreds of unrelated lines.

- **Tests:** mock Vertex/Gemini and OCR; synthetic data only, never real records. The suite is
  `backend/tests/` (`testpaths` is set, so a stray `scripts/*_test.py` is not collected).
  There is **no `--cov-fail-under`** - coverage is reported to SonarCloud and the gate is its
  server-side quality gate, enforced by `sonar.qualitygate.wait=true`. Do not quote a local
  coverage floor as if CI enforced one.

- **Workflow:** PR-based, always; never push to `main`. Commit messages and PR titles use the
  scopes in `.claude/rules/commit-scopes.md` (source of truth - add a scope there in the PR
  that needs it), and commit subjects end with the PR number.
