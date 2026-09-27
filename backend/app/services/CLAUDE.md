# backend/app/services - agent instructions

The pipeline modules. Model providers have their own rules in `llm/CLAUDE.md`.

## Rules

- Never import FastAPI here. Take an explicit SQLAlchemy `Session` where a module needs the
  database; a module used inside a thread pool opens its own short-lived session or receives
  everything it needs resolved beforehand. A `Session` is not thread-safe.
- Every model call goes through the provider seam. Summarize uses `llm.get_provider()`; every
  other stage pairs `llm.provider_for_stage(stage)` with `settings.model_for_stage(stage)`. Mixing
  the two sends one stage's model name over another stage's transport, and nothing reports it.
- Pass each stage's own stage name. A different string silently changes the backend, the model and
  the thinking budget for that call.
- Never log patient content: page text, header fields, filenames, prompts, model responses. Log
  ids, counts, stage names and error types.
- Page text comes from `page_text.py` (the `page_texts` store); do not OCR a page again elsewhere.
  Stored good pages are never re-read, so an OCR change does not affect records already processed.
- The catalog is DB-first (`catalog.py`): once any `categories` row exists the constants in
  `taxonomy.py` and `seed_catalog.py` are ignored for that database, and inserting a single
  category row into an empty table collapses the catalog to that one row. `seed_catalog()` must
  never be called from app code.
- A summary prompt resolves: this category's database row, then its code prompt in `prompts.py`,
  then the General (100) row, then the General code prompt (`catalog.get_prompt`). A `prompts.py`
  edit reaches every category without a row of its own. The prompt is only part of the system
  message: `summarize_engine` prepends shared rule blocks by category.
- Summary rules live in paired places - the generation prompt and the audit's rules. Change both
  or generation and audit drift apart. See `docs/how-to/change-a-summary-prompt-or-rule.md`.
- Title rules in `classification.py` return fixed category ids and are ordered; the order is
  load-bearing. Admin catalog edits cannot change them.
- The segment worker runs these modules from a different image (`mrr-backend-classifier`).
  After changing `classification.py`, `segment_engine.py`, `windows.py` or anything they import,
  rebuild `segment-worker` too, or you are testing the old code.
- Depositions are summarized in three-page groups by the owner's decision, although the human
  reviews use one page per paragraph. Do not change it without that decision.

## Commands

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run pytest -q tests/test_<module>.py
```

Docs: `docs/explanation/*.md` (one page per stage), `docs/reference/model-calls-by-stage.md`.
