# Contributing

This repository holds MRR AI: a Next.js frontend, a FastAPI backend, Postgres, and Redis/RQ
workers that segment, categorize and summarize medical records. It processes patient data, so the
rules on data and secrets below are not optional.

## Branches and the promotion order

| Branch | Receives code from | Merge method |
| --- | --- | --- |
| `main` | Pull requests from feature branches | Squash |
| `staging` | Pull requests from `main`, or a guarded `hotfix/*` branch | Merge commit |
| `production` | Pull requests from `staging` | Merge commit |
| `qwen` | Pull requests from `main` | Merge commit |

- Nobody pushes to these branches directly, administrators included. Every change arrives by pull
  request, and the required checks must pass.
- The promotion guard (`guard-into-<branch>`) refuses a pull request from any other source, so
  code reaches `production` only through `main` -> `staging` -> `production`.
- A hotfix lands on `main` first. When it cannot wait for a full promotion, cherry-pick the commits
  from `main` with `git cherry-pick -x` onto a `hotfix/*` branch and open it against `staging`. The
  guard checks that every commit is a plain cherry-pick naming a commit already on `main` AND makes
  the same change as that commit, file by file. It refuses anything it cannot compare in full: a
  file with no text diff (binary, or too large for the API), a commit whose file list may have been
  cut off, or a pull request with too many commits to list.
- A pull request into `main` must be up to date with `main` before it merges: merge `main` into
  your branch. Never use "Update branch" on a promotion pull request; it would merge the lower
  branch back into the upper one.

The rules and every required check are listed in
[CI and merge gates](docs/reference/ci-and-merge-gates.md).

## Pull requests

- Branch from `main` with a prefix: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/`,
  `ci/`, `perf/`.
- The title becomes the commit on `main`, so it follows the commit format
  `<type>(<scope>): <subject>`: imperative, ASCII, no trailing period, at most 72 characters.
  Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `perf`, `test`, `style`, `ci`, `build`.
  Scopes: [.claude/rules/commit-scopes.md](.claude/rules/commit-scopes.md).
- Fill in every section of the pull request template, including HIPAA / PHI Impact.
- Keep a pull request to one purpose. Documentation changes in the same pull request as the code
  it describes.

## Working locally

- [Run the app locally](docs/how-to/run-the-app-locally.md)
- [Run the tests](docs/how-to/run-the-tests.md)
- [Work on these docs](docs/how-to/work-on-these-docs.md)

Install the pre-commit hooks once with `pre-commit install`. They run ruff, gitleaks and the file
checks in `.pre-commit-config.yaml`, including the size limit that stops a PDF being committed.

## Data and secrets

- Use synthetic data only: in tests, fixtures, examples, screenshots, issues and pull requests.
  Never commit, paste or attach a real medical record, a patient's name or any identifier from one.
- Never commit a secret. Settings come from `.env`, which git ignores; the templates are
  `.env.example` and `deploy/env.docker.example`. Service-account keys go in `secrets/`.
- Model calls go through one seam, `backend/app/services/llm/`. A change to any AI path needs the
  HIPAA section of the pull request template filled in.

## Reporting

- A bug or a feature request: open an issue with the matching form.
- A security vulnerability: follow [SECURITY.md](SECURITY.md). Never open a public issue for one.
- Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
