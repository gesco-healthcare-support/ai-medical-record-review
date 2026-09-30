# How to run the tests

Use this before you push, when CI fails and you want to reproduce it locally, or when you add a
test. It covers the three suites CI runs (backend, frontend, end-to-end) and how to check the
coverage floors CI enforces. What each CI job does and what fails it is in
[CI and merge gates](../reference/ci-and-merge-gates.md).

## Prerequisites

- Docker with the Compose v2 plugin, and a Bash shell (Git Bash on Windows).
- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 for you if needed.
- Node.js 24 with corepack. CI runs Node 24; the frontend `package.json` pins pnpm 9.15.0, which
  corepack provides.
- Free host ports 5432 and 6379 for the test Postgres and Redis.

## Backend tests

### 1. Start the test Postgres and Redis

From the repository root:

```bash
docker compose -p mrrtest -f docker-compose.dev.yml up -d --wait postgres redis
```

`docker-compose.dev.yml` is the test stack: a throwaway Postgres on host port 5432 (password
`mrr_dev_only`) and a Redis on 6379, both separate from the app stack. Always give it its own
project name with `-p mrrtest`, and use the same flag for `down` and `logs`. The file sets no
project name of its own, so without `-p` Compose names the project after the checkout folder,
and in a folder named `mrr` that is the app stack's project name.

Every backend test needs this Postgres, including tests that never touch the database themselves:
an automatic fixture in `backend/tests/conftest.py` deletes leftover test accounts before and after
every test. The job, queue, cancel and download tests also need Redis.

If another program on your machine already serves Redis on port 6379, the test Redis cannot start
and the queue tests run against the other server without any warning, because nothing a Redis
client can ask tells the two apart. Stop the other Redis first.

### 2. Install the dependencies

```bash
cd backend
uv sync --extra docs
```

`--extra docs` is required. It installs the PDF, OCR, Word and model libraries the application
imports at startup; a bare `uv sync` leaves them out and the suite cannot import the app. The
`dev` group (pytest, ruff and the rest) is installed by default. You do not need the
`classifier` extra (PyTorch); CI does not install it either.

### 3. Bring the test database schema up to date

```bash
DATABASE_URL=postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr SECRET_KEY=dev-only-secret SECURITY_PASSWORD_SALT=dev-only-salt uv run alembic upgrade head
```

Alembic reads the database URL from the application settings, which require those three values.
Repeat this after every pull that adds a migration. If you forget, the suite prints a
`[conftest] The test database is at ...` warning at the start and most database tests fail naming a
missing column.

### 4. Run the suite

Serial, as a plain local run:

```bash
uv run pytest -q
```

Parallel, as CI runs it (three workers, each test file kept on one worker):

```bash
uv run pytest -q -n 3 --dist loadfile
```

With CI's coverage options, which also write `backend/coverage.xml`:

```bash
uv run pytest -n 3 --dist loadfile --cov=app --cov-branch --cov-report=xml --cov-report=term-missing
```

One file, or one test:

```bash
uv run pytest -q tests/test_jobs.py
uv run pytest -q tests/test_files.py::test_safe_name_strips_paths_and_traversal
```

Do not set `DATABASE_URL` for pytest. `conftest.py` finds the test database itself from the two
Compose files, prefers port 5432, and refuses to run against the app stack's port 5433 because the
fixtures insert and delete rows. The same check rejects an exported `DATABASE_URL` that names 5433.

A parallel run gives each worker its own copy of the test database (`mrr_gw0`, `mrr_gw1`, ...),
its own Redis database and its own test-account prefix. The copies are made from the `mrr`
database with `CREATE DATABASE ... TEMPLATE`, which needs nothing else connected to `mrr`: close
any `psql` session, other pytest run or host-run API on it first.

### 5. Run the lint gates

CI fails on any of these, and they are separate checks:

```bash
uv run ruff check .
uv run ruff format --check .
uvx pyright==1.1.414
```

pyright type-checks `app/` only, with the settings in `pyproject.toml` `[tool.pyright]`.

Run `uv run ruff format .` to apply the formatting.

## Frontend unit and component tests

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm test
```

`pnpm test` runs Vitest once over every `*.test.ts(x)` and `*.spec.ts(x)` file under `app/`,
`lib/`, `hooks/` and `components/`. Other commands:

| Command | What it does |
| --- | --- |
| `pnpm test:watch` | Vitest in watch mode. |
| `pnpm test:coverage` | One run with coverage; writes `coverage/lcov.info` and `coverage/coverage-summary.json`. |
| `pnpm exec vitest run components/review/stepper.test.tsx` | One file. |
| `pnpm lint` | ESLint over the app's code (config `eslint.config.mjs`); any error or warning fails it. CI runs it. |
| `pnpm typecheck` | TypeScript check (`tsc --noEmit`). CI runs it. |
| `pnpm build` | The production build. CI runs it. |

Two things about the Vitest setup to keep in mind when you add tests:

- A test file outside `app/`, `lib/`, `hooks/` and `components/` is never collected, and nothing
  reports it. The collection list and the coverage list are both in `frontend/vitest.config.mts`
  and must name the same folders.
- All test files share one worker and one jsdom window. Module mocks, `vi.stubGlobal` stubs, the
  DOM and fake timers are reset between files by `frontend/vitest.setup.ts`; anything else you set
  directly on `window`, `document`, `process.env` or a prototype must be restored in the same file.
  A file that ends with fake timers still on fails.

## End-to-end tests

The Playwright specs in `frontend/e2e/` drive a live app stack through the proxy on port 8080.
Playwright does not start the app itself.

1. Bring up and migrate the app stack as in [How to run the app locally](../how-to/run-the-app-locally.md).
   The specs cover sign-in, upload and navigation; they start no AI job, so model credentials are
   not needed.
2. Install the browser once, then run the specs:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
pnpm e2e
```

Each spec registers a fresh account (`e2e+...@example.com`) in the app stack's database and uploads
the synthetic fixture `frontend/e2e/fixtures/sample.pdf`. To run against another address, set
`E2E_BASE_URL` (default `http://localhost:8080`). The HTML report is written to
`frontend/playwright-report/`; open it with `pnpm exec playwright show-report`.

## Check the coverage floors

CI's `coverage-floor` job fails a pull request below these floors:

| Suite | Floor | Measured as |
| --- | --- | --- |
| Backend | 90% | One branch-aware total: (covered lines + covered branches) / (all lines + all branches), from `coverage.xml`. |
| Frontend | 80% | Each of statements, branches, functions and lines separately, from `coverage-summary.json`. |

Locally:

- Backend: run the coverage command in step 4. The `TOTAL` line of the terminal report is the same
  branch-aware figure, rounded for display.
- Frontend: run `pnpm test:coverage` and read the "All files" row, or the `total` block of
  `frontend/coverage/coverage-summary.json`. CI compares the integer counts, so a figure that
  displays as 80.00% can still be below the floor.

The exact computation is in [CI and merge gates](../reference/ci-and-merge-gates.md).

## Verify it worked

- Backend: pytest ends with a summary line of passed tests and no failures or errors, and printed
  no `[conftest]` warning at the start.
- Frontend: Vitest reports every test file passed.
- End-to-end: Playwright lists every spec as passed.

## If it fails

| Symptom | Cause | Fix |
| --- | --- | --- |
| `RuntimeError: The test database (docker-compose.dev.yml, port 5432) is not running` | The test Postgres is down, and the app stack's Postgres is up | Start the test stack (step 1). |
| `RuntimeError: DATABASE_URL names port 5433` | `DATABASE_URL` is exported and points at the app database | `unset DATABASE_URL` |
| `OperationalError` naming the host and port | Nothing answers on the test port, or the credentials are wrong | Start the test stack; do not override its password. |
| `[conftest] The test database is at X, but the migrations head is Y` | The schema is behind | Step 3. |
| `[conftest] Redis at ... did not answer` | Redis is not running | Start the test stack with `redis` (step 1). |
| `Could not copy the test database 'mrr' for worker gw0: something else is connected to it` | Something is connected to `mrr` during a parallel run | Close it, or run serially. |
| Collection fails with `ModuleNotFoundError: No module named 'pypdf'` | Installed without the `docs` extra | `uv sync --extra docs` |
| A test passes in CI but not locally | A local `backend/.env` is read by the settings when you run from `backend/`; CI has none | Move `backend/.env` aside and run again. |

## Clean up

```bash
docker compose -p mrrtest -f docker-compose.dev.yml down
```

Add `-v` to delete the test database volume as well; the next run then needs step 3 again.

## Related pages

- [CI and merge gates](../reference/ci-and-merge-gates.md)
- [Compose services](../reference/compose-services.md)
- [How to create a database migration](../how-to/create-a-database-migration.md)
- [How to extend the frontend](../how-to/extend-the-frontend.md)
- [How to work on these docs](../how-to/work-on-these-docs.md)
