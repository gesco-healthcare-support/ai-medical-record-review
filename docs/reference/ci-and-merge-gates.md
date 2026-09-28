# CI and merge gates reference

Every CI job, what it runs, what fails it, and what a pull request needs before it can merge into
`main`.

Source of truth: `.github/workflows/ci.yml`, `sonar-project.properties`,
`.pre-commit-config.yaml`, and the repository ruleset for `main` (GitHub repository settings, not a
file in the repository).

## Triggers

| Event | Filter | Why |
| --- | --- | --- |
| `push` | Branches `main` and `qwen` | A pushed feature branch is already tested by its pull request; listing push branches avoids a second run. |
| `pull_request` | None: every pull request, whatever its base branch | A base-branch filter would leave pull requests into unlisted branches with no CI at all. |

The workflow has no `schedule`, no `workflow_dispatch`, no `concurrency` group and no
`permissions` block.

## Jobs

All jobs run on `ubuntu-latest`.

| Job | needs | What it runs | Fails when |
| --- | --- | --- | --- |
| `backend` | - | Ruff lint and format check, an import smoke test, migrations, then the pytest suite with branch coverage, against Postgres and Redis service containers | Any lint or format finding, the import fails, a migration fails, or any test fails |
| `frontend` | - | Typecheck, production build, Vitest with coverage | A type error, a build error, or any test fails |
| `e2e` | - | Builds and starts the app stack with Compose (without workers), then Playwright | The app is not ready within the wait loop, or any spec fails |
| `secret-scan` | - | gitleaks over the checked-out files | gitleaks reports a finding |
| `coverage-floor` | `backend`, `frontend` | Reads both coverage reports and compares them with the floors | Either suite is below its floor, or a report is missing, empty or unreadable |
| `sonarcloud` | `backend`, `frontend` | SonarCloud scan with both coverage reports, waiting for the quality gate; on pull requests, a check for new issues and hotspots | The quality gate fails, or (pull requests) the analysis cannot be proven current, or the pull request adds any issue or hotspot |
| `docs` | - | Builds the documentation site strictly | Any MkDocs warning, including a link to a page that does not exist |

A job whose `needs` failed is skipped, not passed; the failed job is the one that blocks.

### `backend`

Working directory `backend/`. Service containers:

| Service | Image | Settings |
| --- | --- | --- |
| `postgres` | `postgres:16` | `POSTGRES_USER=mrr`, `POSTGRES_PASSWORD=mrr_dev_only`, `POSTGRES_DB=mrr`; port 5432; health check `pg_isready -U mrr -d mrr` |
| `redis` | `redis:7` | Port 6379; health check `redis-cli ping` |

| Step | Command |
| --- | --- |
| Checkout | `actions/checkout@v5` |
| Install uv | `astral-sh/setup-uv@v5` with cache |
| Install dependencies | `uv sync --extra docs` (no PyTorch) |
| Lint and format | `uv run ruff check .` then `uv run ruff format --check .` |
| Import smoke | `uv run python -c "from sqlalchemy.orm import configure_mappers; import app.main; configure_mappers(); print('import OK')"` |
| Migrate and test | `uv run alembic upgrade head` then `uv run pytest -n 3 --dist loadfile --cov=app --cov-branch --cov-report=xml --cov-report=term-missing` |
| Upload coverage | Artifact `backend-coverage` (`backend/coverage.xml`) |

### `frontend`

Working directory `frontend/`.

| Step | Command |
| --- | --- |
| Checkout | `actions/checkout@v5` |
| Node | `actions/setup-node@v4`, Node 24 |
| pnpm | `corepack enable` |
| Install | `pnpm install --frozen-lockfile` |
| Typecheck | `pnpm typecheck` |
| Build | `pnpm build` |
| Test | `pnpm test:coverage` |
| Upload coverage | Artifact `frontend-coverage` (`frontend/coverage/lcov.info`, `frontend/coverage/coverage-summary.json`) |

### `e2e`

The segment and summarize workers and Vertex are not started, so the AI flows are not covered.

| Step | Command |
| --- | --- |
| Checkout, Node 24, pnpm | As in `frontend` |
| Build images | `docker compose build api web` |
| Start data services | `docker compose up -d --wait postgres redis` |
| Migrate | `docker compose run --rm api alembic upgrade head` |
| Start the app | `docker compose up -d --wait api web proxy` |
| Wait for readiness | Up to 60 attempts, 2 s apart: `http://localhost:8080/login` must return 200 and the API's `/health` must return 200 when probed inside the `api` container. On timeout, prints the `proxy`, `web` and `api` logs and fails. |
| Install | `pnpm install --frozen-lockfile`, `pnpm exec playwright install --with-deps chromium` |
| Run specs | `pnpm exec playwright test` |
| On failure | Uploads artifact `playwright-report` |
| Always | `docker compose down -v` |

With `CI` set, Playwright forbids `test.only` and retries a failed spec once.

### `secret-scan`

| Step | Command |
| --- | --- |
| Checkout | `actions/checkout@v5` with `fetch-depth: 0` |
| Scan | Downloads the gitleaks 8.30.0 Linux binary from the gitleaks GitHub releases, then `./gitleaks dir . --redact --no-banner --exit-code 1` |

The gitleaks binary is used rather than the gitleaks GitHub Action because the Action needs a paid
licence for organisations.

### `coverage-floor`

Downloads the two coverage artifacts, then checks each half. The job fails closed: a missing, empty
or unparseable report fails it rather than passing.

| Half | Report | Measure | Floor | Comparison |
| --- | --- | --- | --- | --- |
| Backend | `backend/coverage.xml` (Cobertura) | One branch-aware total: `(lines-covered + branches-covered) / (lines-valid + branches-valid)` | 90% | `pct < 90` fails |
| Frontend | `frontend/coverage/coverage-summary.json` | Each of `statements`, `branches`, `functions`, `lines` in `total` | 80% | Integer counts: `covered * 100 >= 80 * total` must hold for all four |

The floors are the `FLOOR` environment values of the two steps in `ci.yml`.

### `sonarcloud`

| Step | What it does |
| --- | --- |
| Checkout | Full history (`fetch-depth: 0`) |
| Download coverage | Both artifacts |
| Normalise paths | Rewrites `<source>app</source>` to `<source>backend/app</source>` in `coverage.xml`, and prefixes each `SF:` line of `lcov.info` with `frontend/`, so SonarCloud can match the reports to files from the repository root |
| Scan | `SonarSource/sonarqube-scan-action@v8.2.0` with `SONAR_TOKEN`. `sonar.qualitygate.wait=true` makes the step fail when the quality gate fails. |
| New-issue check | Pull requests only. See below. |

The new-issue check runs in order and stops at the first failure:

| Check | Source | Fails when |
| --- | --- | --- |
| The analysis belongs to this run | `ceTaskId` from `.scannerwork/report-task.txt`, then `api/ce/task` | The file or task id is missing, the task status is not `SUCCESS`, or the task is for another pull request |
| The pull request exists in SonarCloud | `api/measures/component` | The request returns an HTTP error |
| The analysed commit is the head commit | `api/project_pull_requests/list` | The list carries this pull request with a commit other than the head commit. A list that does not carry it is not a failure. |
| No new issues | `api/issues/search` with `resolved=false` | The count is above 0, or unreadable |
| No new security hotspots | `api/hotspots/search` with `status=TO_REVIEW` | The count is above 0, or unreadable |

Every call uses `curl --fail-with-body`, so an HTTP error fails the step. The job prints each new
issue and hotspot with its rule, file and message. To pass, fix each one or mark it in SonarCloud
with a reason, then re-run the job.

### `docs`

Working directory `docs-site/`.

| Step | Command |
| --- | --- |
| Checkout | Repository checkout |
| Install uv | uv setup |
| Install | `uv sync --frozen` |
| Build | `uv run mkdocs build --strict` |

## SonarCloud project settings (`sonar-project.properties`)

| Property | Value |
| --- | --- |
| `sonar.projectKey` | `gesco-healthcare-support_ai-medical-record-review` |
| `sonar.organization` | `gesco-healthcare-support` |
| `sonar.sources` | `backend/app`, `frontend/app`, `frontend/components`, `frontend/hooks`, `frontend/lib` |
| `sonar.tests` | `backend/tests` |
| `sonar.python.version` | `3.12` |
| `sonar.python.coverage.reportPaths` | `backend/coverage.xml` |
| `sonar.javascript.lcov.reportPaths` | `frontend/coverage/lcov.info` |
| `sonar.coverage.exclusions` | `frontend/app/**` |
| `sonar.exclusions` | `mrr_ai/**`, `experiments/**`, `frontend/.next/**`, `frontend/node_modules/**`, `frontend/public/pdfjs/**`, `frontend/e2e/**`, `**/*.test.ts`, `**/*.test.tsx`, `**/__pycache__/**` |
| `sonar.cpd.exclusions` | `backend/tests/**` |
| `sonar.qualitygate.wait` | `true` |

The analysis is CI-based. SonarCloud's Automatic Analysis must stay disabled for the project, or
the CI scan fails with "running CI analysis while Automatic Analysis is enabled".

## Merge rules for `main`

Set in the repository ruleset, not in `ci.yml`. Values as read from GitHub:

| Rule | Setting |
| --- | --- |
| Changes arrive by pull request | Required |
| Required approving reviews | 0 |
| Allowed merge method | Squash only |
| Branch must be up to date with `main` before merging | Not required |
| Force push (non-fast-forward) | Blocked |
| Branch deletion | Blocked |

### Required status checks

| Check | Posted by |
| --- | --- |
| `backend` | GitHub Actions (`ci.yml`) |
| `frontend` | GitHub Actions (`ci.yml`) |
| `e2e` | GitHub Actions (`ci.yml`) |
| `secret-scan` | GitHub Actions (`ci.yml`) |
| `coverage-floor` | GitHub Actions (`ci.yml`) |
| `sonarcloud` | GitHub Actions (`ci.yml`) |
| `SonarCloud Code Analysis` | SonarCloud's own GitHub integration, from the analysis the `sonarcloud` job uploads |

A job that is not in this list, such as `docs`, still runs and reports on every pull request but
does not block a merge. Adding a job to the list is a change to the ruleset, made by a repository
admin.

## Secrets

| Name | Used by | Purpose |
| --- | --- | --- |
| `SONAR_TOKEN` | `sonarcloud`: the scan step and the new-issue check | Authenticates to SonarCloud |

No other repository secret is referenced. The `SECRET_KEY`, `SECURITY_PASSWORD_SALT` and
`DATABASE_URL` values in `ci.yml` are throwaway test values written into the file, not secrets.

## Artifacts

| Name | Produced by | Consumed by | Contents | Retention |
| --- | --- | --- | --- | --- |
| `backend-coverage` | `backend` | `coverage-floor`, `sonarcloud` | `backend/coverage.xml` | 1 day |
| `frontend-coverage` | `frontend` | `coverage-floor`, `sonarcloud` | `lcov.info`, `coverage-summary.json` | 1 day |
| `playwright-report` | `e2e`, on failure only | People | `frontend/playwright-report` | 3 days |

## Pinned versions

| Tool | Version | Where |
| --- | --- | --- |
| `actions/checkout` | v5 | All jobs |
| `astral-sh/setup-uv` | v5 | `backend`, `docs` |
| `actions/setup-node` | v4, Node 24 | `frontend`, `e2e` |
| `actions/upload-artifact`, `actions/download-artifact` | v5 | Coverage and report artifacts |
| `SonarSource/sonarqube-scan-action` | v8.2.0 | `sonarcloud` |
| gitleaks | 8.30.0 | `secret-scan` (and the pre-commit hook) |
| Postgres, Redis service images | `postgres:16`, `redis:7` | `backend` |

## Local hooks (`.pre-commit-config.yaml`)

Run on each developer's machine once installed; CI does not run pre-commit. Files under
`mrr_ai/static/vendor/` and `frontend/public/pdfjs/` are skipped by every hook.

| Hook | Source and version | What it checks |
| --- | --- | --- |
| `ruff-check` (with `--fix`) | `astral-sh/ruff-pre-commit` v0.15.17 | Python lint, fixing what it can |
| `ruff-format` | `astral-sh/ruff-pre-commit` v0.15.17 | Python formatting |
| `gitleaks` | `gitleaks/gitleaks` v8.30.0 | Secrets in staged changes |
| `detect-private-key` | `pre-commit/pre-commit-hooks` v6.0.0 | Private key material |
| `check-added-large-files` (`--maxkb=1024`) | `pre-commit/pre-commit-hooks` v6.0.0 | Any added file over 1 MB |
| `check-merge-conflict` | `pre-commit/pre-commit-hooks` v6.0.0 | Leftover conflict markers |
| `end-of-file-fixer` | `pre-commit/pre-commit-hooks` v6.0.0 | Missing final newline |
| `trailing-whitespace` | `pre-commit/pre-commit-hooks` v6.0.0 | Trailing spaces |
| `check-toml` | `pre-commit/pre-commit-hooks` v6.0.0 | TOML syntax |
| `check-yaml` | `pre-commit/pre-commit-hooks` v6.0.0 | YAML syntax |

## Related pages

- [How to run the tests](../how-to/run-the-tests.md)
- [How to work on these docs](../how-to/work-on-these-docs.md)
- [Compose services](../reference/compose-services.md)
