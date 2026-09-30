# Commit Scopes (MRR AI)

Allowed commit and PR scopes for this repo (kebab-case), in `<type>(<scope>): <subject>`. Keeps
history greppable. Use the narrowest scope that fits; if none does, add one here in the same PR.

Pipeline stages:

- `ocr` - Tesseract / Poppler, page text store, rasterising
- `segmentation` - sub-document boundaries: windows, the segmentation call, the verify pass
- `categorization` - category assignment: title rules, the cascade, taxonomy and catalog
- `duplicates` - duplicate detection and its review and resolution
- `summarize` - summary generation, the audit, house style, deposition format
- `prompts` - summary and segmentation prompt text
- `export` - Word letter, memo, linked PDF, ZIP, bundles, prepared downloads
- `pipeline` - cross-cutting job flow: queues, lanes, job states, stop/resume, recovery

Application areas:

- `api` - FastAPI routers and schemas
- `auth` - login, sessions, registration, passwords, admin flag
- `admin` - the admin console and admin API (catalog, prompts, reprocess)
- `worker` - the RQ worker process and job functions
- `providers` - the model provider seam and backends (Gemini, OpenAI, vLLM), pacing, preflight
- `config` - `backend/app/config.py` settings and boot guards
- `review` - the /records/[id] workbench (steps, gating, banners, editor)
- `ui` - other frontend pages and shared components, design system
- `backend`, `frontend` - a change spanning several areas of one side

Operations and tooling:

- `compose` - docker-compose.yml: what a container is actually given
- `deploy` - `deploy/` (proxy config, server bootstrap) and deploy procedure
- `ci` - GitHub Actions
- `quality` - linters, formatters, pre-commit, coverage and analysis gates
- `tooling` - uv, pnpm, build, dependency and environment tooling
- `sdk` - third-party SDK swaps and upgrades
- `scripts` - `backend/scripts/` maintenance and dev scripts
- `eval` - `backend/scripts/eval/` measurement harnesses
- `docs` - documentation pages, the docs site, README and CLAUDE files
- `repo` - repository setup and top-level meta files
