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

CI runs the same command in the `docs` job on every pull request. It is a required check on `main`,
so a warning blocks the merge.

The backend suite also runs `backend/tests/test_docs_reference_drift.py`, which fails when a
reference page falls behind the code: a setting missing from
[Configuration](../reference/configuration.md), a route missing from
[HTTP API](../reference/http-api.md), a migration missing from [Migrations](../reference/migrations.md),
a compose service missing from [Compose services](../reference/compose-services.md), a frontend
route missing from [Frontend routes and data](../reference/frontend-routes-and-data.md), or a CI job
missing from the Jobs table of [CI and merge gates](../reference/ci-and-merge-gates.md). If you add
one of those things, add its row in the same pull request.

## How the docs stay current

No check can tell that a sentence became false while every name in it stayed the same. So four
guards work together: exact checks where a fact can be checked, a nudge to re-read where it cannot,
and a weekly list as the backstop.

"The docs that describe a file" is never kept by hand. For any code file it is:

- the `README.md` and `CLAUDE.md` of the nearest folder above the file that has either; and
- every page, README or CLAUDE.md that cites the file's path in backticks.

A citation may be written from the repository root, from the citing file's own folder or any folder
above it, or from the `backend/` or `frontend/` package root. The rules live in one place,
`.github/scripts/docs_guides.py`, and every guard below uses it.

| guard | where | when | what it does |
| --- | --- | --- | --- |
| Exact checks | `backend/tests/test_docs_guides.py`, in the required `backend` job | every pull request | Blocks the merge when a file in a folder is not named in that folder's README (`__init__.py`, dotfiles and lockfiles are exempt), when a doc cites a path that does not exist, when a "`path` `symbol`" citation names a symbol no longer in that file, or when a CLAUDE.md passes 200 lines. |
| Docs to re-read | `docs-impact` job in `.github/workflows/docs-drift.yml` | every pull request | Warns only. For each changed code file (tests and lockfiles excluded) whose describing docs the pull request did not touch, it adds a warning annotation and lists the docs in the job summary. Re-read them; update any the change made wrong, or say in the pull request why none needed it. |
| Weekly drift report | `docs-drift-report` job in the same workflow | Mondays 15:00 UTC, or run it by hand | Keeps one open issue, labelled `docs-drift`, listing every doc whose code has commits after the doc's own last commit. |
| Stop hook | `.claude/hooks/docs-reminder.sh`, registered in `.claude/settings.json` | when a Claude Code agent is about to finish | Reminds the agent once about folders whose code it changed without touching their README, CLAUDE.md or citing pages. |

To clear an entry in the weekly report, re-read the doc against its code. If it is wrong, fix it.
If it is still right, change something in it anyway so the next report starts from that commit. The
convention is a closing line `<!-- reviewed: YYYY-MM-DD -->` (added, or its date updated); it does
not show on the site.

When the Stop hook reminds an agent about a folder whose docs are still right, the agent records
that it looked, and the reminder stays quiet until that folder's code changes again:

```bash
bash .claude/hooks/docs-reminder.sh --reviewed <folder>
```

The record is kept in `.git/docs-reviewed`, on that machine only.

## Conventions every page follows

- **ASCII only.** No curly quotes, long dashes or arrows; write `-` and `->`.
- **Point at code by path and symbol, never by line number.** `backend/app/services/jobs.py` `enqueue()`
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

Keeping them useful to an agent:

- **The file-by-file list belongs in the README, not the CLAUDE.md.** The exact checks keep the
  README list complete. A second copy in CLAUDE.md would only drift and push the file towards its
  size limit.
- **Put in CLAUDE.md only what the agent cannot learn quickly from the code**: the rule that must
  hold, the trap that already cost a bug, the command to run. Everything else is noise it re-reads
  on every visit.
- **Cite code as `path` plus symbol.** The exact checks then catch a rename or deletion the moment
  it happens. Write the symbol exactly as the file spells it, case included: an environment
  variable's upper-case name does not match the lower-case setting that reads it.
- **When a rule stops being true, delete it.** A stale rule in CLAUDE.md is followed; a stale
  sentence on a page is only read.

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
