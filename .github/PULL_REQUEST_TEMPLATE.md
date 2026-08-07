<!--
Gesco MRR AI PR template. Fill every section. See ~/.claude/rules/pr-format.md.
Stack: Next.js (frontend/) + FastAPI (backend/), Postgres, Redis/RQ workers, Vertex/Gemini,
Tesseract + Poppler OCR.
Title format (set above): <type>(<scope>): <subject>  -- 50 target, 72 hard cap, ASCII only, scope required.
Scopes: see .claude/rules/commit-scopes.md - that file is the source of truth, and adding a
scope there is part of the PR that needs it. Do not restate the list here; a copy is what
drifted last time.

HEIGHTENED PHI RISK: This project sends raw medical record content to third-party LLM APIs.
Every change must be evaluated for PHI exposure in prompts, logs, and cached responses.
-->

## Summary
<!-- 1-3 bullets. Plain language, readable by a non-technical stakeholder. -->
-

## Motivation / Context
<!-- Why now? Link to ticket, incident, or vault Decision-Log entry. Keep brief. -->


## Changes
<!-- Grouped by file or area. Skip trivial churn. -->


## Test Plan
- [ ] Python tests pass locally (`pytest` or equivalent)
- [ ] OCR pipeline tested end-to-end on a sample PDF (synthetic only)
- [ ] LLM prompt regression: before/after outputs compared on fixture inputs
- [ ] Manual testing performed (describe what was tested)

## Risk / Rollback
Blast radius:
Rollback:

## Screenshots
<!-- `N/A (no UI change)` unless frontend/app/ or frontend/components/ changed (including
     app/globals.css and app/evaluators-ds.css). Otherwise attach before/after. -->
N/A (no UI change)

## Dependencies
<!-- Lists any backend/pyproject.toml, backend/uv.lock, frontend/package.json or
     frontend/pnpm-lock.yaml changes. Default: None. -->
None

## Breaking change
<!-- If any commit has `!` or `BREAKING CHANGE:`, restate here with migration notes. Default: None. -->
None

## Documentation
- [ ] Feature CLAUDE.md updated (if applicable)
- [ ] docs/ updated (if applicable)
- [ ] Prompt-engineering changelog updated (if prompts changed)
- [ ] No new docs needed

## HIPAA / PHI Impact (STRICT -- this project handles raw PHI)
<!--
This project transmits raw medical record content to OpenAI and Gemini APIs.
Any change to prompts, logging, caching, OCR output handling, or response storage
REQUIRES a narrative paragraph below covering:
- What PHI flows through this change.
- Where it is persisted (logs, cache, DB, temp files).
- Retention policy for any new stored data.
- Which third parties (OpenAI, Gemini) see the data and what their data-use terms say.
-->
- [ ] No real patient data used in tests, fixtures, or examples; synthetic only.
- [ ] New logging does NOT capture raw PDF content, OCR output, or LLM prompt/response bodies.
- [ ] If prompts were changed, they do NOT include PHI in system prompts or few-shot examples.
- [ ] No new PHI persisted to disk beyond the documented upload-and-delete lifecycle.
- [ ] Third-party data-use terms (OpenAI, Gemini) confirmed to match HIPAA BAA requirements for this data type.
- [ ] If a BAA does not cover the API call path, the PR is blocked -- route through approved APIs only.

## Additional Notes
<!-- Anything else reviewers should know? -->


Closes #
