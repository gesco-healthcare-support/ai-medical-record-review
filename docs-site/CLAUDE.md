# docs-site - agent instructions

Tooling that builds `../docs/` into the searchable site served at `/docs/`. Pages are NOT here.

## Rules

- Every page under `docs/` must be listed in `mkdocs.yml` `nav`. The build is `strict`, so an
  unlisted page, a broken relative link or a missing `#anchor` fails CI (`docs` job) and the image.
- Do not loosen `strict`, `validation` or `exclude_docs` to get a build through. Fix the page.
- Keep versions pinned in `pyproject.toml` and locked in `uv.lock`. mkdocs must stay below 2.0:
  2.0 removes plugins and cannot build Material. The successor tool, Zensical, reads this same
  `mkdocs.yml` if a migration is ever needed.
- The Docker build context is the repo root. `Dockerfile.dockerignore` is an allowlist; if the
  build needs a new file, admit it there explicitly. Never admit uploads, secrets or `.env`.
- Site pages link to other pages with relative `.md` links inside `docs/` only. A link to a file
  outside `docs/` fails the strict build: cite code as a backticked path instead.

## Commands

```bash
cd docs-site && uv sync --frozen
uv run --frozen mkdocs build --strict      # what CI and the image run
uv run --frozen mkdocs serve               # live preview at http://127.0.0.1:8000/docs/
docker compose build docs && docker compose up -d --force-recreate docs proxy   # from repo root
```

Writing conventions (Diataxis, ASCII, no hosts or PHI, path + symbol instead of line numbers):
`docs/how-to/work-on-these-docs.md`.

<!-- reviewed: 2026-09-30 -->
