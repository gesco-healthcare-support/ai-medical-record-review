# How to work on these docs

Use this when you add or change a page, change code that a page describes, or need to get a docs
change onto the server.

The pages are plain Markdown in `docs/`. The folder `docs-site/` holds only the tooling that builds
them into this site with Material for MkDocs.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed.
- A clone of the repository.

## Preview the site while you write

```bash
cd docs-site
uv sync --frozen
uv run --frozen mkdocs serve
```

Open <http://127.0.0.1:8000/docs/>. The page reloads each time you save a file under `docs/`.

## Add a page

Every page is one of four kinds, and lives in that kind's folder:

| kind | folder | the reader wants to | shape |
| --- | --- | --- | --- |
| Tutorial | `docs/tutorials/` | learn by doing, start to finish | steps with the result of each |
| How-to | `docs/how-to/` | get one task done | "How to ...", prerequisites, steps, verify, undo |
| Reference | `docs/reference/` | look up an exact fact | "Source of truth:" line, then complete tables |
| Explanation | `docs/explanation/` | understand how and why | the design, its reasons, diagrams |

1. Decide which kind the page is and create it in that folder. One page serves one need; if you
   find yourself writing two kinds, write two pages and link them.
2. Add the page to `nav` in `docs-site/mkdocs.yml`, under its section. The build fails if you do
   not.
3. Link it from at least one related page, with a relative link such as
   `../reference/configuration.md`.
4. Build strictly (next section) and fix every warning.

## Check before you commit

```bash
cd docs-site
uv run --frozen mkdocs build --strict
```

It must exit 0. Every warning is an error, so this fails on:

- a link to a page that does not exist;
- a link to a `#heading` that does not exist on the target page;
- a Markdown file under `docs/` that is not listed in `nav`;
- a relative link to a file outside `docs/`, such as a source file. Write code paths as backticked
  text instead: `backend/app/services/jobs.py`.

CI runs the same command in the `docs` job on every pull request.

The backend suite also runs `backend/tests/test_docs_reference_drift.py`, which fails when a
reference page falls behind the code: a setting missing from
[Configuration](../reference/configuration.md), a route missing from
[HTTP API](../reference/http-api.md), a migration missing from [Migrations](../reference/migrations.md),
a compose service missing from [Compose services](../reference/compose-services.md), or a frontend
route missing from [Frontend routes and data](../reference/frontend-routes-and-data.md). If you add
one of those things, add its row in the same pull request.

## Conventions every page follows

- **ASCII only.** No curly quotes, long dashes or arrows; write `-` and `->`.
- **Point at code by path and symbol, never by line number.** `services/jobs.py` `enqueue()`
  survives an edit; `jobs.py:241` is wrong within a week.
- **One home per fact.** If another page owns a topic, link to it instead of repeating it. Copies
  drift apart.
- **Change the page in the same pull request as the code it describes.**
- **Delete a page that has stopped being true** rather than leaving it with a warning. Historical
  material goes to `legacy/docs/`.
- **This repository is public.** No patient data, no real hostnames or IP addresses, no server
  account names, no email addresses, no personal names. Use placeholders such as `<SERVER_HOST>`.
  Known defects and security weaknesses are tracked outside the repository, not on these pages.
- **Diagrams in Mermaid** (```` ```mermaid ```` fences). They render here and on GitHub.
- **Callouts as blockquotes** starting with a bold label, for example `> **Warning:** ...`, so the
  page reads the same on GitHub. Avoid features that only the site renders.

## README.md and CLAUDE.md in each folder

Every package has both files, and they serve different readers:

- **`README.md`** is for a person browsing the repository: what the folder is for, a table of its
  files, how its code is run and tested, and links to the site pages about it. It does not repeat
  those pages.
- **`CLAUDE.md`** is for an AI coding agent working in that folder. Claude Code loads it only when
  the agent reads a file there. It holds the rules that must hold, the traps that have bitten
  before, and the exact lint and test commands for that area. Keep it under 200 lines: longer files
  are followed less reliably.

When you change a folder's rules or layout, update both files in the same pull request.

## Get a docs change onto the server

The `docs` container serves a copy of the site baked into its image, so the server shows a change
only after a rebuild. With the rest of a deploy, or on its own:

```bash
docker compose build docs
docker compose up -d --force-recreate docs
```

Then check that `http://<SERVER_HOST>:8080/docs/` shows the change. The full deploy procedure is
[Deploy to the server](deploy-to-the-server.md).

## If the site tooling needs replacing

The tools are pinned in `docs-site/pyproject.toml` and `docs-site/uv.lock`. Material for MkDocs is
in maintenance mode: it receives critical fixes until 2027-05-05 and no new features. Its authors'
successor, Zensical, builds the same `mkdocs.yml` and Markdown. Because the site is plain Markdown
plus one config file, moving to another generator does not touch the pages. Keep `mkdocs` below
2.0: version 2.0 removes plugins and cannot build this theme.

## Related pages

- [Architecture](../explanation/architecture.md) - what the pages describe.
- [CI and merge gates](../reference/ci-and-merge-gates.md) - where the `docs` job sits among the checks.
