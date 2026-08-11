# Documentation Index

Docs follow the [Diataxis](https://diataxis.fr/) split: explanation, how-to, reference.

> **Staleness warning.** The app was rewritten from Flask to Next.js + FastAPI. Several docs below
> still describe the old system and are marked **[LEGACY]**. Anything so marked is a historical
> record, not a description of what runs. The pre-rewrite code itself now lives in
> [`legacy/`](../legacy/README.md).

## Start here

- [../CLAUDE.md](../CLAUDE.md) - what the system is, where things live, and the traps. Current.
- [RUNBOOK.md](RUNBOOK.md) - run it locally and deploy it. Current.
- [../legacy/README.md](../legacy/README.md) - what the pre-rewrite app was, and how its parts map
  onto today's.

## Explanation (understand)

- [architecture.md](architecture.md) - **[LEGACY]** package layout and request lifecycle of the Flask
  app. The stage sequence is still broadly right; the code layout is not.
- [explanation/segmentation.md](explanation/segmentation.md) - **[LEGACY]** stage 1. Describes the
  page-map CSV, which is no longer how stages talk - boundaries are rows in Postgres. The chunking
  discussion is superseded by `experiments/a1-segmentation/EXPERIMENT-LOG.md`.
- [explanation/categorization.md](explanation/categorization.md) - stage 2: the B5 cascade
  (rules -> embeddings -> Gemini enum) and the DB-backed, admin-editable catalog. Largely still
  accurate - the cascade survived the rewrite.
- [explanation/summarization.md](explanation/summarization.md) - **[LEGACY]** stages 3-4. Says OpenAI;
  summaries run on Vertex/Gemini by default, with OpenAI behind a config flag.
- [explanation/frontend-ui.md](explanation/frontend-ui.md) - **[LEGACY]** written when a
  backend/frontend split was still a proposal. That split has happened: see `frontend/`.

## How-to (do a task)

- [how-to/run-tests.md](how-to/run-tests.md) - **[LEGACY]** paths. The current suite is
  `cd backend && uv run pytest`, against the dev-stack database on port 5432 - never the app database
  on 5433. See CLAUDE.md.
- [how-to/add-a-category.md](how-to/add-a-category.md) - add or edit a category + summary prompt via
  the admin console. Still accurate.
- [how-to/add-a-blueprint.md](how-to/add-a-blueprint.md) - **[LEGACY]** "blueprint" is a Flask
  concept. To add a route now, add a FastAPI router under `backend/app/api/`.

## Reference (look up)

- [reference/api-routes.md](reference/api-routes.md) - **[LEGACY]** the Flask route table. The live
  API is FastAPI; its generated OpenAPI schema is the reliable source.
- [reference/csv-contract.md](reference/csv-contract.md) - **[LEGACY]** the 6-column page-map CSV.
  Kept because export and the legacy import path still speak this shape, but it is no longer the
  interface between pipeline stages.
- [reference/Categories Jan 25, 2025.docx](reference/) - the category taxonomy (source). Current.
- [reference/prompts/](reference/prompts/) - the original per-category prompt sources. They became
  `backend/app/services/prompts.py`, which has since diverged - the code is the source of truth.
- [reference/macros/](reference/macros/) - Word output-formatting macros.

## Decisions (why)

- [decisions/](decisions/) - Architecture Decision Records. **Historical by design.** Several
  describe the Flask app and are correct as records of what was decided then. Do not rewrite them to
  match the present.

## Research

- [research/Initial-Research.md](research/Initial-Research.md) - **[LEGACY]** the original research on
  segmentation/OCR/summarization. Historical.
- [../experiments/a1-segmentation/](../experiments/a1-segmentation/) - the Page Stream Segmentation
  work. **`EXPERIMENT-LOG.md` is current and worth reading before proposing any segmentation
  change** - several obvious approaches are already measured and rejected there.

## Plans

- [plans/](plans/) - dated feature plans (research -> design -> build). Not every recent plan is
  committed, so this folder is an incomplete record of work done.
