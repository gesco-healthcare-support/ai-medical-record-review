---
feature: Make main's CI test the Next.js + FastAPI rewrite (backend + frontend), retire the legacy Flask CI
date: 2026-07-22
status: in-progress
base-branch: main
related-issues: []
---

## Goal

On `main`, CI runs the rewrite's test suites - backend pytest (on Postgres + Redis) with
coverage, frontend Vitest unit/component tests, and Playwright E2E over the core non-AI flows -
with a SonarCloud new-code coverage gate, and the legacy Flask (`mrr_ai`) jobs are retired.

## Context & decisions

Why now: `main` IS the rewrite (commit `9c17060` cut over; PRs #27-#30 landed on it), but
`.github/workflows/ci.yml` still only exercises the legacy Flask app - so the shipping code
(`backend/`, `frontend/`) has ZERO regression protection. `.github/workflows/rewrite.yml`
already has the right backend job shape but triggers on a now-dead branch
(`feat/nextjs-fastapi-rewrite`), so it never runs. Task task_f204a0c2.

Resolved Open Decisions (via modal 2026-07-22):
- Decision: **retire the legacy Flask jobs** (drop `mrr_ai` pytest + `import app` smoke; repoint
  SonarCloud off `mrr_ai`) because `main` is the rewrite and `mrr_ai/` is being phased out;
  CI should reflect what ships. `mrr_ai/` + root `tests/` stay in-tree, just un-gated.
- Decision: **SonarCloud new-code coverage >=80%** (matches Patient Portal, see memory
  patient-portal-ci-coverage-gate) because it protects future changes without forcing backfill on
  existing code. The threshold itself is a SERVER-side quality gate in the SonarCloud project UI;
  CI feeds coverage (backend `coverage.xml` + frontend `lcov.info`) and waits on the gate.
- Decision: **Vitest + React Testing Library** for unit/component AND **Playwright** for E2E
  (two complementary layers, not two redundant unit runners) because Vitest is Next.js's
  documented App-Router unit stack (verified: `vercel/next.js` docs `testing/vitest.mdx`, HIGH)
  and Playwright is the standard for real-browser flows; Adrian delegated the runner choice,
  prioritizing regression protection.
- Decision (flagged, mine): **E2E covers the non-AI flows only** (auth, upload, document
  list/open/delete, navigation, admin gating) against a docker-compose SUBSET
  (`postgres redis api web proxy` - no torch workers, no Vertex) because CI cannot run Vertex
  (auth/quota/no-BAA) and the torch image is heavy/flaky; the AI identify/summarize paths stay
  live-manually-verified (as they are today). Alternative rejected: mock Vertex in-container -
  more moving parts for a path we already verify by hand on real docs.
- Decision (flagged, mine): **fold rewrite.yml's jobs into ci.yml and delete rewrite.yml**
  (orphaned, never fires) so there is ONE `CI` workflow on `main`.

## All needed context

Versions / tooling (verified in-repo):
- Backend: `backend/pyproject.toml` - core deps + `docs`/`classifier` extras; dev group =
  `pytest>=8.3, pytest-asyncio>=0.24, httpx>=0.28, ruff>=0.8`. **No `pytest-cov`** (must add).
  `[tool.pytest.ini_options] asyncio_mode = "auto"`. ruff line-length 100, target py312.
- Backend tests: `backend/tests/` - **134 tests** collect cleanly (21 files). `conftest.py:20-23`
  sets `DATABASE_URL`/`SECRET_KEY`/`SECURITY_PASSWORD_SALT`/`ENVIRONMENT` via `setdefault` (CI env
  wins); `conftest.py:28-29` selects the Windows selector loop (no-op on Linux CI). Integration
  tests hit Postgres `:5432`; RQ tests (`test_jobs.py`) enqueue to real Redis `:6379`. Requires
  `alembic upgrade head` first.
- Frontend: `frontend/package.json` - scripts `dev/build/start/typecheck` only; **no test tooling**.
  Next 15.1, React 19, TS 5.7, pnpm 9.15, `radix-ui`, TanStack Query. `next.config.ts` uses
  `output: "standalone"` + a dev/prod `/api/:path*` rewrite to `API_ORIGIN`. Client components
  confirmed (`"use client"` in all `components/review/*.tsx`), so Vitest+jsdom renders them.
- Compose (`docker-compose.yml`, project `mrr`): services `postgres` (host 5433->5432),
  `redis`, `api` (uvicorn :8000, `--extra docs`, lazy Vertex client - boots with empty
  GOOGLE_* env), `segment-worker` (torch), `summarize-worker`, `web` (Next standalone, talks to
  `api:8000` via `API_ORIGIN`), `proxy` (nginx :8080 -> /api api:8000, / web:3000).
  `x-backend-env` requires `SECRET_KEY` + `SECURITY_PASSWORD_SALT` (`${VAR:?...}`).
- SonarCloud: `sonar-project.properties` - `sonar.sources=mrr_ai`, `sonar.tests=tests`,
  `sonar.python.coverage.reportPaths=coverage.xml`, `qualitygate.wait=true`. Project key
  `gesco-healthcare-support_ai-medical-record-review`, org `gesco-healthcare-support`.
- `.gitignore` (root) already ignores `.coverage` + `coverage.xml` (matches `backend/` too).
  `frontend/.gitignore` = `node_modules/ .next/ out/ next-env.d.ts *.tsbuildinfo .env*.local`
  (needs coverage + playwright entries).

Patterns to mirror:
- Backend CI job: `.github/workflows/rewrite.yml:12-76` (PG:16 + Redis:7 services + `uv sync
  --extra docs` + ruff + import smoke + `alembic upgrade head` + pytest). Copy shape, add coverage.
- Frontend build/typecheck job: `.github/workflows/rewrite.yml:78-100` (node 24 + corepack +
  `pnpm install --frozen-lockfile` + typecheck + build). Extend with tests.
- Backend coverage config: root `pyproject.toml` `[tool.coverage.run]`/`[tool.coverage.report]`
  (source, omit tests, `relative_files=true`, exclude_lines) - mirror into `backend/pyproject.toml`.
- Vitest config: `vercel/next.js` `testing/vitest.mdx` (`vitest.config.mts` with
  `@vitejs/plugin-react` + `vite-tsconfig-paths` + `environment: 'jsdom'`).

Gotchas:
- SonarCloud coverage path resolution is finicky: coverage.xml written from `backend/` has
  `app/...` paths but sources live at `backend/app`. Verify the PR scan reports non-zero new-code
  coverage; if paths mismatch, adjust `sonar.python.coverage.reportPaths` / coverage source prefix
  (empirical, resolved while watching CI to green - not an open design question).
- `pnpm build` crashes LOCALLY on Windows Node 24 (WasmHash); it is GREEN in CI (Linux). Do NOT
  gate local validation on `pnpm build` - run it in CI only.
- E2E job builds the `api` + `web` images (uv sync + next build) - slow (~several min). Acceptable
  for a dedicated job; buildx layer-cache is a follow-up optimization, not in scope.
- Register requires a strong password (8+ / digit / symbol) + a name; E2E registers fresh unique
  accounts (no seeding, no real salt needed). All E2E fixtures are synthetic (HIPAA).

## Test authoring method (how each test is made to enforce CORRECT behavior)

The failure mode for test-after tests is echoing what the code already does - the test passes
but verifies nothing (or cements a bug). Every test below follows this discipline:

1. **Expected values come from an INDEPENDENT source of truth, never the implementation under
   test:**
   - `rowErrors` <- the documented rule + its server twin `app/services/rows.py` (two
     implementations of ONE rule; testing both against one hand-written rule table catches
     client/server drift as a real bug).
   - `status-pill` <- the hang-fix design (memory `mrr-ai-pipeline-hang-fix` +
     `worker/failures.py`): the user-facing label each job state MUST show.
   - `use-review-workflow` <- the documented state machine (boot routes by `status`/`active_job`;
     `enableSummaries:false` routes a done doc to the editor, never summaries).
   - E2E <- the user-visible flow (what the user sees before AND after each action).
2. **Hand-compute the expected side of every assertion** - do not paste back what the code returns.
3. **Falsifiability**: for each load-bearing assertion, do a manual mutation check - flip the
   expected value or comment out the branch and confirm the test goes RED. A test never seen to
   fail is not yet a test. (Automated mutation testing is a deferred follow-up, see Tooling note.)
4. **Cover boundaries + known past bugs**, not just the happy path (off-by-one; the overlap rule;
   the `category_01` `or "1"` class of always-truthy bug; paused / needs-attention routing).
5. **Assert observable output/contract, not internals**; resilient locators
   (`getByRole`/`getByText`) in E2E; Given-When-Then structure.
6. **If code contradicts the contract, STOP and surface it** (Chesterton's Fence) - never rewrite
   the test to match suspected-buggy code; flag it to Adrian.

Coverage (>=80% new-code) proves lines are EXECUTED; this method is what proves they are CORRECT.
Coverage is necessary, not sufficient.

## Tasks (implementation blueprint)

### T1 - Backend coverage + property-testing tooling  [approach: code]
- what: MODIFY `backend/pyproject.toml` - add `pytest-cov>=6.0` and `hypothesis>=6.100` to
  `[dependency-groups] dev`; add `[tool.coverage.run]` (`source = ["app"]`,
  `omit = ["tests/*", "*/__pycache__/*"]`, `relative_files = true`) and `[tool.coverage.report]`
  (`exclude_lines` for `pragma: no cover`, `if __name__ == .__main__.:`, `raise NotImplementedError`;
  `show_missing = true`). Run `uv lock` to refresh `backend/uv.lock`.
- pattern: root `pyproject.toml` `[tool.coverage.run]`/`[tool.coverage.report]`.
- acceptance (EARS): WHEN `uv run pytest --cov=app --cov-report=xml` runs from `backend/`, THE
  SYSTEM SHALL exit 0 and write `backend/coverage.xml` measuring the `app` package.

### T1b - Backend property-based validation test  [approach: tdd]
- what: CREATE `backend/tests/test_rows_property.py` - Hypothesis strategies generate row lists +
  `total_pages`; assert INVARIANTS of `app.services.rows.validate_rows` (category set stubbed so
  only the range/overlap/integer rules are under test): (1) contiguous, ascending,
  non-overlapping integer rows within `[1, total_pages]` -> `None`; (2) any row with
  `start <= previous_end` -> a non-None error containing "overlaps"; (3) a non-integer
  `start`/`end` -> an error containing "integers"; (4) a GAP between rows never causes an error.
  Use `@given` + `@settings(max_examples=...)`; shrink gives the minimal counterexample.
- pattern: Hypothesis + pytest (`@given(...)` over `st.lists(st.fixed_dictionaries(...))`); the
  rule is `app/services/rows.py:14-32`.
- acceptance (EARS): WHEN `uv run pytest tests/test_rows_property.py` runs, THE SYSTEM SHALL find
  no counterexample to the four invariants across the generated inputs. IF `validate_rows` ever
  accepts an overlap or rejects a legal gap, THE SYSTEM SHALL fail with the shrunk minimal case.

### T2 - Backend CI job (on main)  [approach: code]
- what: MODIFY `.github/workflows/ci.yml` - add a `backend` job (working-directory `backend`) that
  mirrors `rewrite.yml:12-76`: `postgres:16` + `redis:7` services, `uv sync --extra docs`,
  `ruff check .` + `ruff format --check .`, import smoke (`configure_mappers(); import app.main`
  with dummy DB URL + secrets), `alembic upgrade head`, then
  `uv run pytest --cov=app --cov-report=xml --cov-report=term-missing`; upload `backend/coverage.xml`
  as artifact `backend-coverage`.
- pattern: `.github/workflows/rewrite.yml:12-76`.
- acceptance (EARS): WHEN a push or PR targets `main`, THE SYSTEM SHALL run the backend suite on a
  fresh Postgres + Redis and fail the job if any test fails or ruff reports issues.

### T3 - Frontend Vitest harness  [approach: code]
- what: MODIFY `frontend/package.json` - add devDeps `vitest`, `@vitejs/plugin-react`,
  `vite-tsconfig-paths`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`,
  `@testing-library/user-event`, `@vitest/coverage-v8`, `@fast-check/vitest`; add scripts
  `"test": "vitest run"`,
  `"test:watch": "vitest"`, `"test:coverage": "vitest run --coverage"`. CREATE
  `frontend/vitest.config.mts` (`plugins: [tsconfigPaths(), react()]`, `test.environment: 'jsdom'`,
  `test.setupFiles: ['./vitest.setup.ts']`, `test.globals: true`, coverage provider `v8`,
  reporters `['text','lcov']`, `coverage.include` = `lib/** hooks/** components/**`). CREATE
  `frontend/vitest.setup.ts` (`import '@testing-library/jest-dom/vitest'`; `vi.mock('next/navigation')`
  returning stub `useRouter`/`usePathname`/`useSearchParams`; stub `next/font` if a rendered
  component imports it). Run `pnpm install` to refresh `frontend/pnpm-lock.yaml`.
- pattern: `vercel/next.js` docs `testing/vitest.mdx`.
- acceptance (EARS): WHEN `pnpm test` runs in `frontend/`, THE SYSTEM SHALL discover and execute
  the Vitest suites under jsdom and exit 0 when they pass.

### T4 - Frontend unit + component tests  [approach: test-after]
- what: CREATE tests for the highest-value stable units (read each source first at build):
  - `frontend/lib/review-rows.test.ts` - `rowErrors` (valid rows; overlap -> "overlaps the
    previous document"; `start<1`/`end>total`/`start>end` -> range msg; non-integer -> "pages
    must be numbers"; gaps between docs allowed; `previousEnd` tracking); `sortRows` (by start
    then end); `withKeys`/`newKey` (unique keys); `stripKeys` (drops `_key`). Anchor:
    `frontend/lib/review-rows.ts:37-53`.
  - `frontend/hooks/use-review-workflow.test.tsx` - `renderHook` inside a `QueryClientProvider`
    with `review-api` mocked; assert the boot routing + state transitions and that
    `enableSummaries:false` routes a done doc to the editor (not summaries). Anchor:
    `frontend/hooks/use-review-workflow.ts` (read for exact contract).
  - `frontend/components/documents/status-pill.test.tsx` - each document status (incl. the
    paused / needs-attention states from the hang fix) maps to the expected label + class.
    Anchor: `frontend/components/documents/status-pill.tsx` (read for the status map).
  - `frontend/components/review/rows-table.test.tsx` - renders one row per record, shows the
    error hint on an invalid row, reflects the include checkbox. Anchor:
    `frontend/components/review/rows-table.tsx`.
  - `frontend/lib/review-rows.property.test.ts` - `@fast-check/vitest` `test.prop` generates row
    lists + `totalPages` and asserts INVARIANTS of `rowErrors` (the client twin of the T1b server
    rule): contiguous ascending in-range integer rows -> empty map; any `start <= previousEnd` ->
    that row index present in the map; a GAP between documents -> no error for that row.
- pattern: Vitest test in `testing/vitest.mdx` (`render` + `screen` from `@testing-library/react`);
  `@fast-check/vitest` `test.prop([...])` for the property test.
- acceptance (EARS) - each suite MUST include these exact cases:
  - `rowErrors`: IF `start < 1` OR `end > totalPages` OR `start > end`, THEN the row index maps
    to the range message `needs 1 <= start <= end <= <totalPages>`. IF a value is non-integer,
    THEN it maps to "pages must be numbers". IF `start <= previousEnd` (overlap), THEN it maps to
    "overlaps the previous document". WHERE there is a GAP between two documents (skipped pages),
    THE SYSTEM SHALL return NO error for that row. WHEN all rows are contiguous and in range, THE
    SYSTEM SHALL return an empty map. (Boundaries `start=1`, `end=totalPages`, `end=totalPages+1`,
    `start=end`, `start=end+1` are all asserted.)
  - `sortRows`: WHEN given rows out of order, THE SYSTEM SHALL return them ascending by `start`
    then `end`, without mutating the input array. `withKeys`/`newKey`: keys are unique;
    `stripKeys`: the `_key` field is absent from every returned row.
  - `use-review-workflow`: WHERE `enableSummaries:false` AND the document is done, THE SYSTEM
    SHALL route to the editor step, NOT summaries; boot routes to the step implied by
    `status`/`active_job`.
  - `status-pill`: WHEN doc.status is uploaded/segmenting/reviewing/summarizing/done/error/
    interrupted, THE SYSTEM SHALL render the exact label + tone from doc-table.js (e.g. reviewing
    -> "Ready for review" + hd-badge-warning; done -> "Summarized" + -success). WHEN a running job
    has a total, THE SYSTEM SHALL append " (current/total)".
    DEVIATION (surfaced while building): `paused` is a JobState, not a DocumentStatus, so it is
    not a pill concern. `needs_attention` IS a real DocumentStatus (types.ts:40) but has NO entry
    in the pill's label/tone maps -> it currently renders the raw "needs_attention" string +
    neutral tone (a genuine UX gap the missing tests let through). Kept OUT of this tests-only PR
    to preserve its clean revert; flagged as a separate UX fix (spawn_task), NOT asserted as
    correct here.
  - `rows-table`: WHEN a row is invalid, THE SYSTEM SHALL show its error hint; the include
    checkbox reflects the row's included flag.
  - `review-rows.property`: WHEN fast-check generates inputs, THE SYSTEM SHALL find no
    counterexample; IF `rowErrors` ever misses an overlap or flags a legal gap, THE SYSTEM SHALL
    fail with the shrunk minimal case.
  - WHEN `pnpm test:coverage` runs, THE SYSTEM SHALL pass all suites and emit
    `frontend/coverage/lcov.info`. Each suite has >=1 assertion proven to fail under a manual
    mutation (per Test authoring method #3).

### T5 - Frontend CI job (typecheck + build + unit tests)  [approach: code]
- what: MODIFY `.github/workflows/ci.yml` - add a `frontend` job (working-directory `frontend`,
  node 24, `corepack enable`, `pnpm install --frozen-lockfile`) running `pnpm typecheck`,
  `pnpm build`, `pnpm test:coverage`; upload `frontend/coverage/lcov.info` as artifact
  `frontend-coverage`.
- pattern: `.github/workflows/rewrite.yml:78-100`.
- acceptance (EARS): WHEN a push or PR targets `main`, THE SYSTEM SHALL typecheck, production-build,
  and unit-test the frontend, failing the job on any error.

### T6 - Playwright harness  [approach: code]
- what: MODIFY `frontend/package.json` - add devDep `@playwright/test`; add script
  `"e2e": "playwright test"`. CREATE `frontend/playwright.config.ts` (`testDir: './e2e'`,
  `use.baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080'`, `projects: [chromium]`,
  `reporter: [['list'], ['html', { open: 'never' }]]`, `use.trace: 'on-first-retry'`, NO
  `webServer` - the app stack is started externally). CREATE `frontend/e2e/fixtures/sample.pdf`
  (a tiny SYNTHETIC multi-page PDF).
- pattern: standard `@playwright/test` config.
- acceptance (EARS): WHEN `pnpm exec playwright test` runs against a live stack at the baseURL,
  THE SYSTEM SHALL execute the `e2e/` specs in Chromium.

### T7 - Playwright E2E specs (non-AI flows)  [approach: test-after]
- what: CREATE under `frontend/e2e/`:
  - `auth.spec.ts` - register a fresh unique account (`e2e+<random>@example.com`, strong pw,
    name) -> lands authenticated on My Documents; sign out; sign back in.
  - `documents.spec.ts` - upload `fixtures/sample.pdf` -> appears in the table; open it -> the
    review page renders (stepper + start/identify panel); delete it -> row gone. (No identify -
    the AI path is not exercised.)
  - `navigation.spec.ts` - a non-admin account does NOT see the Admin nav item; `/diagnostics`
    and `/depositions` load their picker.
- pattern: `@playwright/test` `page.goto(baseURL)` + role/text locators + `expect`.
- acceptance (EARS) - each spec asserts a state CHANGE (before AND after), not mere presence:
  - auth: WHEN a fresh account registers, THE SYSTEM SHALL land authenticated on My Documents
    (assert the heading/URL). WHEN the user signs out, THE SYSTEM SHALL redirect to `/login`.
    WHEN they sign back in, THE SYSTEM SHALL return to My Documents.
  - documents: WHEN `fixtures/sample.pdf` is uploaded, THE SYSTEM SHALL add exactly one new row
    to the table. WHEN that row is opened, THE SYSTEM SHALL render the review stepper + the
    start/identify panel. WHEN it is deleted, THE SYSTEM SHALL remove the row (count decremented).
  - navigation: WHERE the account is non-admin, THE SYSTEM SHALL NOT show the Admin nav item;
    `/diagnostics` and `/depositions` SHALL render their document picker.
  - WHERE a flow needs Vertex (identify / summarize), THE SYSTEM SHALL NOT exercise it in CI.

### T8 - E2E CI job (compose subset)  [approach: code]
- what: MODIFY `.github/workflows/ci.yml` - add an `e2e` job that: writes CI env
  (`SECRET_KEY` >=32 chars, `SECURITY_PASSWORD_SALT`, `ENVIRONMENT=dev`, empty `GOOGLE_*`);
  `docker compose up -d --build postgres redis`; wait Postgres healthy;
  `docker compose run --rm api alembic upgrade head`; `docker compose up -d --build api web proxy`;
  poll `http://localhost:8080` until 200; in `frontend/` `pnpm install --frozen-lockfile` +
  `pnpm exec playwright install --with-deps chromium` + `pnpm exec playwright test`; on failure
  upload `frontend/playwright-report/`; always `docker compose down -v`.
- pattern: `docker-compose.yml` service subset (no `segment-worker`/`summarize-worker`).
- acceptance (EARS): WHEN a push or PR targets `main`, THE SYSTEM SHALL build and boot the app
  stack (proxy + api + web + Postgres + Redis), run the E2E specs against `:8080`, and fail the
  job if any spec fails. IF the proxy does not answer 200 within the timeout, THE SYSTEM SHALL
  fail the job (not hang).

### T9 - SonarCloud repoint + coverage feeds  [approach: code]
- what: MODIFY `sonar-project.properties` - `sonar.sources=backend/app`, `sonar.tests=backend/tests`,
  `sonar.python.coverage.reportPaths=backend/coverage.xml`,
  `sonar.javascript.lcov.reportPaths=frontend/coverage/lcov.info`, `sonar.exclusions` to drop
  `mrr_ai/**` and exclude `frontend/.next/**,frontend/node_modules/**,experiments/**`; keep
  `qualitygate.wait=true` + `sonar.python.version=3.12`. MODIFY `.github/workflows/ci.yml`
  `sonarcloud` job - `needs: [backend, frontend]`, download `backend-coverage` -> `backend/` and
  `frontend-coverage` -> `frontend/coverage/`, then run the scan.
- pattern: existing `sonar-project.properties` + `ci.yml:90-107`.
- acceptance (EARS): WHEN the sonarcloud job runs on a PR, THE SYSTEM SHALL submit backend +
  frontend coverage and fail IF the SonarCloud new-code coverage gate (>=80%) is not met.
- note: the 80% threshold is set in the SonarCloud project UI (Adrian confirms, like Patient
  Portal); this task only wires sources + coverage + gate-wait.

### T10 - Retire legacy jobs + delete orphaned workflow  [approach: code]
- what: MODIFY `.github/workflows/ci.yml` - REMOVE the legacy `quality` job (root `uv sync` +
  tree-wide ruff + Flask `import app`) and the legacy `tests` job (`pytest --cov=mrr_ai
  --cov-fail-under=85`); KEEP `secret-scan` (whole-repo gitleaks, still valid). Net jobs:
  `backend`, `frontend`, `e2e`, `secret-scan`, `sonarcloud`. DELETE
  `.github/workflows/rewrite.yml` (orphaned; logic folded into ci.yml).
- pattern: n/a (removal).
- acceptance (EARS): WHEN CI runs on `main`, THE SYSTEM SHALL NOT run any `mrr_ai`/Flask test or
  import, and `rewrite.yml` SHALL no longer exist.

### T11 - gitignore for test artifacts  [approach: code]
- what: MODIFY `frontend/.gitignore` - add `/coverage`, `/test-results`, `/playwright-report`,
  `/blob-report`, `/.playwright`. (Root already ignores `.coverage`/`coverage.xml`.)
- pattern: existing `frontend/.gitignore`.
- acceptance (EARS): WHEN tests generate coverage or Playwright reports, THE SYSTEM SHALL leave
  `git status` clean of those artifacts.

## Validation loop

Local (what I can run without the full stack):
- Backend (needs the dev TEST stack on `:5432`/`:6379` - `docker compose -f docker-compose.dev.yml
  up -d`, ASK first per env boundary): `cd backend && uv sync --extra docs && uv run ruff check .
  && uv run ruff format --check . && uv run alembic upgrade head && uv run pytest --cov=app
  --cov-report=term-missing` -> 134+ pass, coverage printed.
- Frontend unit (no DB): `cd frontend && pnpm install && pnpm typecheck && pnpm test:coverage`
  -> suites pass, `coverage/lcov.info` written. (Skip local `pnpm build` - Windows crash; CI runs it.)
- E2E local (optional, needs docker; ASK before bringing up stacks): `docker compose up -d --build
  postgres redis && docker compose run --rm api alembic upgrade head && docker compose up -d --build
  api web proxy` then `cd frontend && E2E_BASE_URL=http://localhost:8080 pnpm exec playwright test`.
- Quality review of the new tests: run the `pr-review-toolkit:pr-test-analyzer` subagent on the
  diff before opening the PR; act on any real gap it surfaces.

CI (authoritative): push branch `chore/ci-cover-rewrite`, open PR into `main`, watch all jobs
(`backend`, `frontend`, `e2e`, `secret-scan`, `sonarcloud`) to green; confirm SonarCloud reports
non-zero coverage and the new-code gate passes (per watch-ci-after-opening-pr).

## Tooling / augmentations (decided 2026-07-22)

Adopted NOW (in this plan):
- **Property-based testing** on the one real domain rule (row range/overlap validation):
  `@fast-check/vitest` (T4) client-side + `hypothesis` (T1b) server-side. State the invariant,
  let the tool generate hundreds of inputs and shrink any failure to a minimal counterexample -
  the strongest available check that the validation logic is CORRECT, not just present.
- Free build-time aids (no repo change): the `pr-review-toolkit:pr-test-analyzer` subagent to
  grade the new tests before the PR; the Playwright plugin/MCP to author + verify the E2E specs.

Deferred follow-up (NOT in this plan - revisit once the suites exist and are stable):
- **Automated mutation testing** - StrykerJS (TS, `testRunner: "vitest"`) + cosmic-ray (Python,
  cross-platform; mutmut needs WSL on Windows). It mutates code and checks a test fails - the
  automated form of the manual falsifiability step. 2026 guidance: run it TIERED (local /
  scheduled / changed-files), never as a per-PR gate (it re-runs the suite many times).
- The `/hypothesis` Claude Code command (Hypothesis team) as an optional test-authoring aid.
- Sources: stryker-mutator.io, pypi.org/p/cosmic-ray, mutmut.readthedocs.io,
  npmjs.com/package/@fast-check/vitest, hypothesis.readthedocs.io, hypothesis.works claude-code-plugin.

## Post-build verification (2026-07-22)

Ran a 6-lens adversarial review of the suite + CI (20 findings). Addressed the real ones:
- **HIGH (fixed):** SonarCloud coverage was inert - backend coverage.xml (`<source>app</source>` +
  `filename="config.py"`) and frontend lcov (`SF:components/...`) paths did not resolve against
  `sonar.sources` from the repo root, so Sonar imported ZERO coverage (green-but-blind gate).
  Fixed in the sonarcloud job by rewriting the paths to repo-root-relative (`sed`) before the scan;
  verified against the real report formats.
- Added the missing regression guards: hook `needs_attention` / paused / error routing + the
  autosave data-integrity gate (invalid rows never PUT); negative range property tests (client +
  server); client non-integer property; rowErrors multi-error + previousEnd tracking; rows-table
  invalid-row IDENTITY (not just count) + gap-boundary + review-flag; E2E now assert page-specific
  content (start panel, bundle builder heading). `frontend/app` added to `sonar.coverage.exclusions`
  (E2E-only routes); e2e readiness probe now also checks the api, not just the web tier.
- Parity wording corrected: client and server share the range/overlap/gap rule but differ (client
  rejects non-integers, server coerces via `int()`; client collects all errors, server first-only;
  category is server-only).

Two real behaviors surfaced (OUT OF SCOPE here - candidates for the error/validation hardening pass):
- `int("3.5")==3` on the server silently truncates a fractional page; the client rejects it. A
  direct API call bypasses the client gate. Decide whether the server should reject fractional pages.
- On BOOT, if an active summarize job ends `needs_attention`, `watchSummarize`'s `rows` closure is
  still `[]` (state not yet propagated) -> routes to the start panel instead of the editor+notice.
  Minor; the realistic click-Summarize path routes correctly.

Local evidence: 48 frontend tests pass; `tsc` clean; backend ruff clean + `validate_rows`
negative-range behavior confirmed. Backend pytest + E2E execute in CI (isolated services).

## Risk / rollback

Blast radius: the CI pipeline only. No application runtime code changes - only test files, CI
workflow, tooling devDeps (lockfile updates), and Sonar config. Deleting `rewrite.yml` is safe
(it never fires). Retiring the legacy jobs removes `mrr_ai` coverage, acceptable (being phased
out; still in-tree, just un-gated).
Rollback: revert the PR (single squash commit) - restores the prior `ci.yml` + `rewrite.yml` +
Sonar config verbatim.
Watch-outs surfaced during CI-watch, not blockers: SonarCloud coverage path resolution (adjust
reportPaths if new-code coverage reads 0%); E2E image-build time (optimize with buildx cache
later if it's painful).
