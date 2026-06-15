# Documentation Index

Docs follow the [Diataxis](https://diataxis.fr/) split: explanation, how-to, reference.

## Explanation (understand)
- [architecture.md](architecture.md) - package layout, request lifecycle, the 4-stage pipeline, state model, PHI data flow.

## How-to (do a task)
- [RUNBOOK.md](RUNBOOK.md) - run the app and retrieve outputs.
- [how-to/run-tests.md](how-to/run-tests.md) - run the test suite and coverage.
- [how-to/add-a-category.md](how-to/add-a-category.md) - add a document category + prompt.
- [how-to/add-a-blueprint.md](how-to/add-a-blueprint.md) - add a route module.

## Reference (look up)
- [reference/api-routes.md](reference/api-routes.md) - every HTTP endpoint.
- [reference/csv-contract.md](reference/csv-contract.md) - the 6-column page-map CSV.
- [reference/Categories Jan 25, 2025.docx](reference/) - the category taxonomy (source).
- [reference/prompts/](reference/prompts/) - per-category prompt sources.
- [reference/macros/](reference/macros/) - Word output-formatting macros.

## Decisions (why)
- [decisions/](decisions/) - Architecture Decision Records (ADRs).

## Research
- [research/Initial-Research.md](research/Initial-Research.md) - verified research on segmentation/OCR/summarization.
- [../experiments/a1-segmentation/](../experiments/a1-segmentation/) - Page Stream Segmentation spike (chunking fix).
