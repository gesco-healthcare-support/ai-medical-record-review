# docs-site - builds the documentation site

The pages live in [`../docs/`](../docs/index.md) as plain Markdown. This folder holds only the
tooling that turns them into a searchable website with
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/), and the image that serves it
at `/docs/` behind the app's proxy.

| file | what it is |
| --- | --- |
| `mkdocs.yml` | Site configuration: theme, Markdown extensions, strict validation and the `nav` (every page must be listed). |
| `pyproject.toml`, `uv.lock` | The pinned build tooling (mkdocs 1.6.1, mkdocs-material 9.7.7). |
| `Dockerfile` | Two stages: build the site with `--strict`, then serve it from `nginx:1.27`. Build context is the repo root. |
| `Dockerfile.dockerignore` | The ignore file BuildKit uses for this Dockerfile only: admits `docs/` and this folder's build files, nothing else. |
| `nginx.conf` | nginx inside the `docs` container. |

## Preview while writing

```bash
cd docs-site
uv sync --frozen
uv run --frozen mkdocs serve
```

Then open <http://127.0.0.1:8000/docs/> - the dev server serves the site under its `/docs/` path,
like the box does. Pages reload as you save.

## Check before you commit

```bash
cd docs-site
uv run --frozen mkdocs build --strict
```

CI runs the same command in the `docs` job. Any warning fails it: a broken link, a missing anchor,
or a Markdown file under `docs/` that is not in `nav`.

## On the server

The `docs` service in `docker-compose.yml` builds this image; the proxy routes `/docs/` to it.
The image is baked, so a docs change reaches the box only after
`docker compose build docs` and `docker compose up -d --force-recreate docs`.

The full guide to writing and structuring pages is `docs/how-to/work-on-these-docs.md` on the site.
