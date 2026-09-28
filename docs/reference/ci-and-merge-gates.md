# CI and merge gates reference

Every CI job, what it runs and what fails it, and what a pull request needs before it can merge into
each long-lived branch.

Source of truth:

- the four workflows in `.github/workflows/` (`ci.yml`, `guard-tests.yml`, `promotion-guard.yml`,
  `docs-drift.yml`);
- `.github/scripts/promotion_guard.py` and `.github/scripts/docs_guides.py`;
- `backend/scripts/ci/lint_new_migrations.py`;
- `sonar-project.properties` and `.pre-commit-config.yaml`;
- the repository rulesets (GitHub repository settings, not files in the repository).

`backend/tests/test_docs_reference_drift.py` fails when a job in any workflow is missing from the
Jobs table below.

## Workflows and triggers

| Workflow | Event | Filter | Why |
| --- | --- | --- | --- |
| `ci.yml` | `push` | Branches `main` and `qwen` | A pushed feature branch is already tested by its pull request; listing push branches avoids a second run. |
| `ci.yml` | `pull_request` | None: every pull request, whatever its base branch | A base-branch filter would leave pull requests into unlisted branches with no CI at all. |
| `guard-tests.yml` | `pull_request`; `push` to `main` | None on pull requests | Tests the promotion guard's own code before it merges; the guard itself always runs the default branch's copy. |
| `docs-drift.yml` | `pull_request`; `schedule` (Mondays 15:00 UTC); `workflow_dispatch` | None | Two docs guards that never block a merge: warnings on a pull request, and a weekly drift issue. See [How to work on these docs](../how-to/work-on-these-docs.md#how-the-docs-stay-current). |
| `promotion-guard.yml` | `pull_request_target` (`opened`, `synchronize`, `reopened`, `edited`) | None; the check only matters on branches whose ruleset requires it | Runs the default branch's copy of the guard, so a pull request cannot edit the check that judges it. `edited` re-runs it when a pull request's base changes. |

Every workflow gives its jobs a read-only token (`permissions: contents: read`, or none at the
workflow level with per-job grants). No job pushes, comments or approves. The only extra grants
are:

- `osv-scan`: `security-events: write` (and `actions: read`), which its reusable workflow needs to
  upload results;
- the promotion guard: `pull-requests: read`;
- `docs-drift-report`: `issues: write`, to edit its one issue. It is the only job that writes, and it
  runs only on the default branch (`schedule`, `workflow_dispatch`), never on pull request code.

Only `docs-drift.yml` has a `schedule` and a `workflow_dispatch`. No workflow has a `concurrency`
group.

## Jobs

All jobs run on `ubuntu-latest`.

| Job | Workflow | needs | What it runs | Fails when |
| --- | --- | --- | --- | --- |
| `backend` | `ci.yml` | - | Ruff lint and format check, an import smoke test, migration checks (one head; on pull requests, Squawk on the SQL of new migrations), migrations, `alembic check`, then the pytest suite with branch coverage, against Postgres and Redis service containers | Any lint or format finding, the import fails, a migration check fails, a migration fails, the models need a migration nobody wrote, or any test fails |
| `frontend` | `ci.yml` | - | Typecheck, production build, Vitest with coverage | A type error, a build error, or any test fails |
| `e2e` | `ci.yml` | - | Builds and starts the app stack with Compose (without workers), then Playwright | The app is not ready within the wait loop, or any spec fails |
| `secret-scan` | `ci.yml` | - | gitleaks over the checked-out files | gitleaks reports a finding |
| `docs` | `ci.yml` | - | Builds the documentation site strictly | Any MkDocs warning, including a link to a page that does not exist |
| `workflow-lint` | `ci.yml` | - | actionlint and zizmor over `.github/workflows/` | A workflow syntax or expression error, or a zizmor security finding |
| `dependency-review` | `ci.yml` | - | Pull requests only. GitHub's dependency review of the pull request's dependency changes | The pull request adds, or upgrades to, a runtime dependency with a known high or critical vulnerability |
| `osv-scan` | `ci.yml` | - | Pull requests only. OSV-Scanner over `backend/uv.lock` and `frontend/pnpm-lock.yaml`, base against head | The pull request introduces a known vulnerability |
| `coverage-floor` | `ci.yml` | `backend`, `frontend` | Reads both coverage reports and compares them with the floors | Either suite is below its floor, or a report is missing, empty or unreadable |
| `sonarcloud` | `ci.yml` | `backend`, `frontend` | SonarCloud scan with both coverage reports, waiting for the quality gate; on pull requests, a check for new issues and hotspots | The quality gate fails, or (pull requests) the analysis cannot be proven current, or the pull request adds any issue or hotspot |
| `guard-tests` | `guard-tests.yml` | - | The promotion guard's unit tests (`python3 -m unittest -v test_promotion_guard` in `.github/scripts/`) | Any test fails |
| `docs-impact` | `docs-drift.yml` | - | Pull requests only. The docs rules' unit tests, then `python3 .github/scripts/docs_guides.py impact <base> <head>` | Its unit tests fail. The check itself never fails: a changed code file whose describing docs were not touched gets a warning annotation and a row in the job summary. |
| `docs-drift-report` | `docs-drift.yml` | - | Weekly and by hand, never on a pull request. `python3 .github/scripts/docs_guides.py report`, then creates or edits the one open issue labelled `docs-drift` | The report or the `gh` call errors |
| `promotion-guard` | `promotion-guard.yml` | - | Checks that a pull request into a promotion branch comes from the branch above it. Its check is named `guard-into-<base branch>`. | The pull request's head is not an allowed source for its base (see [Promotion order](#promotion-order)) |

A job whose `needs` failed is skipped, not passed; the failed job is the one that blocks. A job that
runs on pull requests only (`dependency-review`, `osv-scan`, `docs-impact`) is skipped on a push.

### `backend`

Working directory `backend/`. Service containers:

| Service | Image | Settings |
| --- | --- | --- |
| `postgres` | `postgres:16` | `POSTGRES_USER=mrr`, `POSTGRES_PASSWORD=mrr_dev_only`, `POSTGRES_DB=mrr`; port 5432; health check `pg_isready -U mrr -d mrr` |
| `redis` | `redis:7` | Port 6379; health check `redis-cli ping` |

| Step | Command |
| --- | --- |
| Checkout | `actions/checkout` with `fetch-depth: 2`, so the migration lint can compare with the parent commit |
| Install uv | `astral-sh/setup-uv` with cache |
| Install dependencies | `uv sync --extra docs` (no PyTorch) |
| Lint and format | `uv run ruff check .` then `uv run ruff format --check .` |
| Import smoke | `uv run python -c "from sqlalchemy.orm import configure_mappers; import app.main; configure_mappers(); print('import OK')"` |
| One migration head | `uv run --no-sync alembic heads` must print exactly one head. Two pull requests that each add a migration pass on their own and leave `main` with two heads, which `alembic upgrade head` refuses. |
| Lint new migrations | Pull requests only. `uv run --no-sync python scripts/ci/lint_new_migrations.py HEAD^1` renders the SQL of each migration the pull request adds (offline, no database) and runs Squawk on it. The rules left out, and why, are in the script's docstring. |
| Migrate and test | `uv run alembic upgrade head`, then `uv run alembic check` (fails when the models need a migration nobody wrote), then `uv run pytest -n 3 --dist loadfile --cov=app --cov-branch --cov-report=xml --cov-report=term-missing` |
| Upload coverage | Artifact `backend-coverage` (`backend/coverage.xml`) |

A new migration must render offline (`alembic upgrade <down>:<rev> --sql`). One that reads rows must
skip those reads when `context.is_offline_mode()` is true, or the lint step fails and says so. See
[How to create a database migration](../how-to/create-a-database-migration.md).

### `frontend`

Working directory `frontend/`.

| Step | Command |
| --- | --- |
| Checkout | `actions/checkout` |
| Node | `actions/setup-node`, Node 24 |
| pnpm | `corepack enable` |
| Install | `pnpm install --frozen-lockfile` |
| Typecheck | `pnpm typecheck` |
| Build | `pnpm build` |
| Test | `pnpm test:coverage` |
| Upload coverage | Artifact `frontend-coverage` (`frontend/coverage/lcov.info`, `frontend/coverage/coverage-summary.json`) |

### `e2e`

The segment and summarize workers and the model providers are not started, so the AI flows are not
covered.

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
| Checkout | `actions/checkout` with `fetch-depth: 0` |
| Scan | Downloads the gitleaks 8.30.0 Linux binary from the gitleaks GitHub releases, then `./gitleaks dir . --redact --no-banner --exit-code 1` |

`gitleaks dir .` scans the files as they are at the checked-out commit, not the git history. The
gitleaks binary is used rather than the gitleaks GitHub Action because the Action needs a paid
licence for organisations. GitHub's own secret scanning and push protection are also enabled on the
repository.

### `docs`

Working directory `docs-site/`.

| Step | Command |
| --- | --- |
| Checkout | `actions/checkout` |
| Install uv | `astral-sh/setup-uv` with cache |
| Install | `uv sync --frozen` |
| Build | `uv run --frozen mkdocs build --strict` |

### `workflow-lint`

| Step | Command |
| --- | --- |
| Checkout | `actions/checkout` |
| actionlint | Downloads actionlint 1.7.12, checks the tarball against its published SHA-256, then `./actionlint -color` |
| zizmor | `uvx zizmor==1.30.1 --persona regular .github/workflows/` |

Both tools are pinned to exact versions because the CI pipeline is itself an attack path: a
compromised action or linter runs with the job's token.

### `dependency-review`

Pull requests only. `actions/dependency-review-action` with `fail-on-severity: high`,
`fail-on-scopes: runtime` and `comment-summary-in-pr: never`. It judges only the pull request's own
dependency changes, so an advisory published against an existing dependency never fails an
unrelated pull request. Development tooling is out of scope because it is not shipped.

### `osv-scan`

Pull requests only. A call to Google's reusable workflow
`osv-scanner-reusable-pr.yml` from the `google/osv-scanner-action` repository, with
`--lockfile=./backend/uv.lock` and `--lockfile=./frontend/pnpm-lock.yaml`. It scans the base and
the head, and fails only on vulnerabilities the pull request introduces. It reads both lockfiles
directly, so it also covers `uv.lock`. Its check name is `osv-scan / osv-scan`.

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
| Scan | `SonarSource/sonarqube-scan-action` v8.2.0 with `SONAR_TOKEN`. `sonar.qualitygate.wait=true` makes the step fail when the quality gate fails. |
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

### `guard-tests` and `promotion-guard`

`guard-tests` runs the unit tests in `.github/scripts/test_promotion_guard.py` against the pull
request's own code. It is the only place a change to the guard is tested before it merges.

`promotion-guard` checks out only the default branch's `.github/scripts/` (never the pull
request's code), then runs `python3 .github/scripts/promotion_guard.py`. The rules it applies are
under [Promotion order](#promotion-order). The decision logic is in the script and its docstring;
the workflow file explains why `pull_request_target` is safe here.

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

## Branches and merge rules

Code moves `main` -> `staging` -> `production`, and `qwen` is fed from `main`. Each branch has
its own repository ruleset, and release tags `v*` have one too. The rules live in GitHub's
settings, not in any file. Read the live values with:

```bash
gh api repos/gesco-healthcare-support/ai-medical-record-review/rulesets --jq '.[] | "\(.id) \(.name)"'
gh api repos/gesco-healthcare-support/ai-medical-record-review/rulesets/<id>
```

| Rule | `main` | `staging`, `production`, `qwen` |
| --- | --- | --- |
| Changes arrive by pull request | Required | Required |
| Allowed merge method | Squash only | Merge commit only, so a promotion keeps the commits of the branch above |
| Required approving reviews | 1 by design; set to 0 from 2026-09-28 to 2026-09-30 | 1 by design; set to 0 from 2026-09-28 to 2026-09-30 |
| Stale approvals dismissed on a new push | Yes | Yes |
| Branch must be up to date before merging | Not required | Not required |
| Force push (non-fast-forward) | Blocked | Blocked |
| Branch deletion | Blocked | Blocked |
| Bypass | Nobody | Nobody |

GitHub never lets an account approve its own pull request. While the approval count is 1, a pull
request needs a second person's approval.

The `v*` tag ruleset blocks moving (updating) or deleting any release tag.

### Required status checks

| Check | Posted by | Required on |
| --- | --- | --- |
| `backend` | GitHub Actions (`ci.yml`) | all four branches |
| `frontend` | GitHub Actions (`ci.yml`) | all four branches |
| `e2e` | GitHub Actions (`ci.yml`) | all four branches |
| `secret-scan` | GitHub Actions (`ci.yml`) | all four branches |
| `coverage-floor` | GitHub Actions (`ci.yml`) | all four branches |
| `sonarcloud` | GitHub Actions (`ci.yml`) | all four branches |
| `workflow-lint` | GitHub Actions (`ci.yml`) | all four branches |
| `SonarCloud Code Analysis` | SonarCloud's own GitHub integration, from the analysis the `sonarcloud` job uploads | `main` |
| `dependency-review` | GitHub Actions (`ci.yml`) | `main` |
| `osv-scan / osv-scan` | GitHub Actions (`ci.yml`, reusable workflow) | `main` |
| `docs` | GitHub Actions (`ci.yml`) | `main` |
| `guard-into-staging` | GitHub Actions (`promotion-guard.yml`) | `staging` |
| `guard-into-production` | GitHub Actions (`promotion-guard.yml`) | `production` |
| `guard-into-qwen` | GitHub Actions (`promotion-guard.yml`) | `qwen` |

`guard-tests`, `docs-impact` and `docs-drift-report` are not required checks. Adding a check to a
branch's list is a change to that branch's ruleset, made by a repository admin.

### Promotion order

`promotion_guard.py` allows, and only from this repository (a fork can name a branch `main` too):

| Base | Allowed head |
| --- | --- |
| `staging` | `main`, or a `hotfix/*` branch that passes the hotfix check |
| `production` | `staging` |
| `qwen` | `main` |
| any other base | anything; no ruleset requires the check there |

The hotfix check:

- Every commit in the pull request must be a plain (one-parent) cherry-pick, made with `git
  cherry-pick -x`.
- Its `(cherry picked from commit <sha>)` trailer must name a commit already on `main`.
- It must make the same change as that commit: per file, the same added and removed lines.
- Anything the check cannot compare is refused: a binary file, a file list the API may have cut
  short, or a pull request over 100 commits.

So a fix always lands on `main` first.

## Secrets

| Name | Used by | Purpose |
| --- | --- | --- |
| `SONAR_TOKEN` | `sonarcloud`: the scan step and the new-issue check | Authenticates to SonarCloud |

No other repository secret is referenced. The `SECRET_KEY`, `SECURITY_PASSWORD_SALT` and
`DATABASE_URL` values in `ci.yml` are throwaway test values written into the file, not secrets.
`workflow-lint` and the promotion guard use the job's own read-only `github.token`.

## Artifacts

| Name | Produced by | Consumed by | Contents | Retention |
| --- | --- | --- | --- | --- |
| `backend-coverage` | `backend` | `coverage-floor`, `sonarcloud` | `backend/coverage.xml` | 1 day |
| `frontend-coverage` | `frontend` | `coverage-floor`, `sonarcloud` | `lcov.info`, `coverage-summary.json` | 1 day |
| `playwright-report` | `e2e`, on failure only | People | `frontend/playwright-report` | 3 days |

## Pinned versions

Every action is pinned to a full commit SHA, with its version in a comment beside the pin. A tag
can be moved to different code; a SHA cannot. Pin a new action the same way. `workflow-lint` runs
zizmor, which audits the workflows for unpinned actions among other problems.

| Tool | Version | Where |
| --- | --- | --- |
| `actions/checkout` | v5.1.0 | All jobs; always with `persist-credentials: false` |
| `astral-sh/setup-uv` | v5.4.2 | `backend`, `docs`, `workflow-lint` |
| `actions/setup-node` | v4.4.0, Node 24 | `frontend`, `e2e` |
| `actions/upload-artifact`, `actions/download-artifact` | v5.0.0 | Coverage and report artifacts |
| `actions/dependency-review-action` | v5.0.0 | `dependency-review` |
| `google/osv-scanner-action` reusable workflow | v2.6.0 | `osv-scan` |
| `SonarSource/sonarqube-scan-action` | v8.2.0 | `sonarcloud` |
| gitleaks | 8.30.0 | `secret-scan` (and the pre-commit hook) |
| actionlint | 1.7.12, checksum verified | `workflow-lint` |
| zizmor | 1.30.1 | `workflow-lint` |
| Squawk | `squawk-cli` 2.66.0 | `backend` (migration lint) |
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
- [How to create a database migration](../how-to/create-a-database-migration.md)
- [How to work on these docs](../how-to/work-on-these-docs.md)
- [Compose services](../reference/compose-services.md)
