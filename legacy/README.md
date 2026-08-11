# legacy/ - the pre-rewrite Flask application

**Nothing in this folder runs.** It is kept for reference only, and moved here on 2026-08-11 so
that neither a human nor an AI assistant reads it as the current system.

## What it is

The original MRR AI: a single-process Flask app (Python 3.12) serving on port **5010**, with
server-rendered templates, a page-map CSV as the interface between pipeline stages, and OpenAI for
summarization. It is what the project was before the Next.js + FastAPI rewrite.

| here                                   | the app that actually runs                    |
| -------------------------------------- | --------------------------------------------- |
| `legacy/app.py` (`app = create_app()`) | `backend/app/main.py` (FastAPI, uvicorn)      |
| `legacy/mrr_ai/blueprints/`            | `backend/app/api/` (FastAPI routers)          |
| `legacy/mrr_ai/services/`              | `backend/app/services/`                       |
| `legacy/mrr_ai/templates/`, `static/`  | `frontend/` (Next.js)                         |
| `legacy/tests/`                        | `backend/tests/`                              |
| `legacy/Dockerfile`                    | `backend/Dockerfile`, `frontend/Dockerfile`   |
| page-map CSV between stages            | Postgres rows (`review_rows`) + Redis/RQ jobs |

## Why it was kept rather than deleted

Parts of the current backend were ported from here, and several modules still carry
"ported from mrr_ai/..." comments pointing at this code as the original. Keeping it makes those
comments checkable. Nothing imports it: the only references from `backend/` are in comments.

## Traps if you read this code

- **The four `CLAUDE.md` files under `mrr_ai/` and `tests/` describe THIS app**, not the current
  one. They were accurate when written and are now historical.
- **`mrr_ai/services/gemini.py` holds a stale copy of the segmentation prompt.** The live one is
  `backend/app/services/gemini.py`. There is a third copy in the separate legacy checkout on `P:`
  that the `experiments/a1-segmentation` harness used to import - see that folder's history.
  Editing the copy in here changes nothing.
- The root `pyproject.toml` still declares this package for coverage and pytest paths, because it
  also carries the repo-wide ruff config that `experiments/` depends on. Those legacy-specific
  settings now point in here.

## If you want it gone

`git rm -r legacy/` and drop the legacy entries from the root `pyproject.toml`. History keeps it.
It was moved rather than deleted only to preserve the "ported from" trail; once nobody is reading
those comments, this folder has no reason to exist.
