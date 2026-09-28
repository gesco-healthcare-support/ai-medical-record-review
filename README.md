# MRR AI - AI Medical Record Review

Turns a large scanned medical-record PDF (hundreds to a few thousand pages) into a reviewed,
summarized Medical Record Review. The app finds the sub-documents in the record, categorizes them,
lets a reviewer correct everything, checks for duplicates, summarizes each sub-document with a
category-specific prompt, and exports the review. A reviewer corrects the machine's work at every
stage: the app is an assistant, not an autopilot.

> **Patient data.** This app processes real medical records. Never commit PDFs, OCR text, exports
> or Word files - their filenames alone can carry patient surnames. Sample and labelled data live
> outside the repository. Every pull request carries a PHI review section.

## Documentation

The full documentation is a searchable site built from [`docs/`](docs/index.md):

- on a running stack: <http://localhost:8080/docs/> (and `/docs/` on the server);
- on GitHub: the Markdown files under [`docs/`](docs/index.md) read as they are;
- locally while writing: see [`docs-site/README.md`](docs-site/README.md).

Start with:

| if you want to | read |
| --- | --- |
| understand the system | [Architecture](docs/explanation/architecture.md) |
| run it on your machine | [Run the app locally](docs/how-to/run-the-app-locally.md), then the [first-day tutorial](docs/tutorials/first-day.md) |
| run the tests | [Run the tests](docs/how-to/run-the-tests.md) |
| deploy it | [Deploy to the server](docs/how-to/deploy-to-the-server.md) and [Back up and restore](docs/how-to/back-up-and-restore.md) |
| look something up | [Glossary](docs/reference/glossary.md), [HTTP API](docs/reference/http-api.md), [Configuration](docs/reference/configuration.md) |

## Quick start

```bash
cp deploy/env.docker.example .env               # then set SECRET_KEY and SECURITY_PASSWORD_SALT
docker compose build
docker compose up -d --wait postgres redis
docker compose run --rm api alembic upgrade head   # create or update the schema
docker compose up -d
```

Open <http://localhost:8080>. The details - which values are required, how to generate the
secrets, how model credentials are supplied - are in
[Run the app locally](docs/how-to/run-the-app-locally.md).

## Repository layout

| path | what it is |
| --- | --- |
| [`backend/`](backend/README.md) | FastAPI API, RQ workers, SQLAlchemy models, Alembic migrations, the pipeline services and their tests. |
| [`frontend/`](frontend/README.md) | Next.js app: the documents list, the record workbench, bundles and admin. |
| [`deploy/`](deploy/README.md) | The nginx proxy config and the one-time server bootstrap script. |
| [`docs/`](docs/index.md) | The documentation pages. |
| [`docs-site/`](docs-site/README.md) | The tooling that builds `docs/` into the site. |
| `docker-compose.yml` | The app stack (the same file runs locally and on the server). |
| `docker-compose.dev.yml` | The throwaway test database and Redis for the backend suite. |
| `deploy/env.docker.example`, `.env.example` | Environment templates: the first is the minimal set for the container stack, the second lists every tunable setting with its reasoning. |
| [`experiments/`](experiments/a1-segmentation/README.md) | Segmentation research; `experiments/a1-segmentation/EXPERIMENT-LOG.md` records what was measured and rejected. |
| [`legacy/`](legacy/README.md) | The pre-rewrite Flask app and its old docs. **Nothing there runs.** |
| `pyproject.toml`, `uv.lock`, `serve.py` (repo root) | Leftovers of the Flask app. The backend's own project is `backend/pyproject.toml`; do not run `uv sync` or `pytest` from the repo root. |

## Contributing

Changes arrive by pull request into `main`; direct pushes are blocked and every required CI check
must pass. Commit messages and PR titles follow `<type>(<scope>): <subject>` with the scopes listed
in [`.claude/rules/commit-scopes.md`](.claude/rules/commit-scopes.md). Update the docs in the same
pull request as the code they describe - see
[Work on these docs](docs/how-to/work-on-these-docs.md). The checks each pull request must pass are
in [CI and merge gates](docs/reference/ci-and-merge-gates.md).

AI coding assistants: read [`CLAUDE.md`](CLAUDE.md) first; each folder has its own `CLAUDE.md`
with the rules for that area.
