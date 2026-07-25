---
feature: Summary faithfulness verify pass (auto-fix + flag)
date: 2026-07-24
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Every generated summary is automatically checked by a second LLM pass (temp 0.0) that rewrites it
to remove statements unsupported by, or contradicting, its own OCR source; the raw output, the
fixed text, and the list of issues are all stored, and summaries the AI actually changed carry a
flag so the reviewer can re-check them.

## Context & decisions
Why now: the A/B on real duplicates (memory `mrr-ai-summary-quality-eval`) proved the temperature
change fixed run-to-run consistency but did NOT clearly reduce fabrication/contradiction (problem
#3); a per-summary verify pass is the remaining lever. Ships as PR 1 of 2 (verify first), then the
duplicate-clustering feature (`2026-07-24-duplicate-clustering-review.md`).

Resolved decisions:
- Decision: verify runs AUTOMATICALLY as a second stage of every summary generation, because #3 is
  a systemic faithfulness gap, not an opt-in concern.
- Decision: the AI AUTO-FIXES the summary AND we keep the raw original, because a medical-legal
  record must retain the untouched model output (training data + audit) while the reviewer reads
  the corrected one.
- Decision: UI shows a FLAG only when the AI changed something (no diff/revert UI in this PR),
  because Adrian chose the minimal surface; full data is stored so a diff/revert can be added later.
- Decision: storage = new `Summary.verified_text` (fixed body) + `Summary.verify_issues` (JSON list)
  + `Summary.verified` (bool); `text` stays the immutable raw output. `effective_text()` precedence
  becomes edited_text > verified_text > text, because reviewer edits always win, then the AI fix,
  then the raw.
- Decision: verify lives INSIDE `summarize_row` behind a `verify` param (default from settings), so
  the main summarize job AND the single-row Re-summarize both get it for free; `bundle_summarize`
  passes `verify=False` to stay fast (decision: bundle export is a bounded quick path).
- Decision: reuse `summary_model` at temp 0.0 with a new hardcoded `VERIFY_PROMPT`; add a
  `summary_verify` kill-switch (default True) so a regression reverts via env with no redeploy. Do
  NOT reuse `verify_model`/`verify_use_text`/`verify_suspect_cap` - those are segmentation-only.
- Decision: +1 LLM call per summarized row on every run is accepted (Adrian), noted in Risk.

## All needed context
- `backend/app/services/summarize_engine.py`: `summarize_row` (:64) returns the legacy output dict;
  `_generate` (:50) is the single generate_with_retry seam; `HARDENING_PREAMBLE` (:35) is the
  prepend-a-constant pattern to mirror for `VERIFY_PROMPT`. Summary body is generated at
  `settings.summary_temperature` (:89); `sourceText` (the OCR input) is already in the output (:102).
- `backend/app/services/verify_pass.py`: mirror its structured-output call shape (temp 0.0 +
  `response_mime_type`/`response_schema`, :114-119) for a JSON-returning verify call - but this is a
  NEW module; do not touch verify_pass.py (segmentation).
- `backend/app/worker/tasks.py`: `_build_summary` (:164) maps the output dict -> `Summary`;
  `summarize_document` persists per row at :369. Add the new fields in `_build_summary`.
- `backend/app/api/documents.py`: `resummarize` (:462) calls `summarize_row` (:506) then copies
  fields onto the Summary (:510-517) - add the verified fields there too.
- `backend/app/services/bundles.py`: `bundle_summary_entries` (used by `bundle_summarize`,
  documents.py:736) - pass `verify=False` (confirm its call into summarize_row during build).
- `backend/app/models.py`: `Summary` (:290); `text` (:299) immutable raw; `edited_text` (:303);
  `effective_text` (:316); `listing` (:319). Add columns after `source_text` (:300).
- `backend/app/config.py`: `summary_temperature` (:46), `summary_model` (:37); add `summary_verify`
  near them; `get_settings` is `@lru_cache` (:142).
- Alembic: migrations live in `backend/alembic/versions/`; generate with
  `uv run alembic revision -m "..."` then hand-edit (additive columns, nullable/defaults).
- Frontend: `frontend/components/review/summaries-view.tsx` renders summary cards + the existing
  `manualCheck` badge (mirror it for a "verified/fixed" flag); the summary shape comes from
  `Summary.listing()` via `GET /{id}/summaries`. Types live near the hook `hooks/use-summaries`.
- Gotchas: `text` must stay raw (comment at documents.py:436 - training data). The DOI/tag
  decoration is applied in summarize_row (:92-100) AFTER the body; verify the BODY, then keep the
  same decoration so `verified_text` is display-ready. LLM judge/verify is noisy - the prompt must
  say "change ONLY to remove unsupported/contradicting content; if faithful, return it unchanged".

## Tasks (implementation blueprint)
1. MODIFY `backend/app/config.py` - add `summary_verify: bool = True` (env `SUMMARY_VERIFY`) with a
   WHY comment near `summary_temperature`.
   - pattern: `summary_temperature` field (:46).
   - approach: code.
   - acceptance (EARS): WHEN settings load, THE SYSTEM SHALL expose `summary_verify` defaulting True
     and overridable by the `SUMMARY_VERIFY` env var.

2. CREATE `backend/app/services/summary_verify.py` - `VERIFY_PROMPT` constant + `verify_summary(model,
   source_text, summary_text) -> dict` returning `{"fixed_text": str, "issues": list[dict]}` via one
   `generate_with_retry` call at temp 0.0 with a `response_schema` (JSON: fixed_text + issues[]
   where each issue = {type: "unsupported"|"contradiction", detail: str}). On any parse/model failure
   return `{"fixed_text": summary_text, "issues": []}` (fail-safe: never worsen the summary).
   - pattern: `_generate` (summarize_engine.py:50) + structured config (verify_pass.py:114-119).
   - approach: tdd (pure parsing + fail-safe branches; PHI-free synthetic tests).
   - acceptance (EARS): WHEN given a summary containing a statement absent from the source, THE
     SYSTEM SHALL return a `fixed_text` without that statement and a non-empty `issues` list. WHEN
     the summary is fully supported, THE SYSTEM SHALL return `issues == []` and `fixed_text` equal to
     the input. IF the model call or JSON parse fails, THEN THE SYSTEM SHALL return the original
     summary text with `issues == []`.

3. MODIFY `backend/app/services/summarize_engine.py` `summarize_row` - add `verify: bool | None =
   None`; resolve to `settings.summary_verify` when None; after the body is generated, when verify is
   on, call `verify_summary(model, text, summary)` and add `output["verifiedText"]` (the fixed body,
   re-decorated with the same DOI prefix as summaryText) and `output["verifyIssues"]` (the issues
   list, or None when unchanged). `summaryText` stays the raw body. Title path unchanged.
   - pattern: existing decoration at summarize_engine.py:92-100.
   - approach: test-after.
   - acceptance (EARS): WHILE `summary_verify` is True, THE SYSTEM SHALL populate `verifiedText` and
     `verifyIssues` in the summarize_row output. WHERE `verify=False` is passed, THE SYSTEM SHALL
     skip the verify call and leave `verifiedText`/`verifyIssues` absent.

4. MODIFY `backend/app/models.py` `Summary` - add `verified` (Boolean, default False), `verified_text`
   (Text, nullable), `verify_issues` (JSON, nullable) after `source_text`. Update `effective_text()`
   to return edited_text if set, else verified_text if set, else text. Update `listing()` to include
   `verified`, `verifyIssues` (the list), and a derived `verifyChanged` (bool: verified and issues
   non-empty).
   - pattern: `effective_text`/`listing` (:316,:319); `manual_check` column (:305).
   - approach: code.
   - acceptance (EARS): WHEN a Summary has a `verified_text` and no `edited_text`, THE SYSTEM SHALL
     return `verified_text` from `effective_text()`. WHEN it also has `edited_text`, THE SYSTEM SHALL
     return `edited_text`.

5. CREATE alembic migration in `backend/alembic/versions/` - add the three `summaries` columns
   (additive, nullable / server_default false for `verified`).
   - pattern: an existing additive migration in `backend/alembic/versions/`.
   - approach: code.
   - acceptance (EARS): WHEN `alembic upgrade head` runs on a DB with existing summaries, THE SYSTEM
     SHALL add the columns without data loss and leave existing rows `verified=false`.

6. MODIFY `backend/app/worker/tasks.py` `_build_summary` (:164) - set `verified=bool(output.get(
   "verifyIssues") is not None or output.get("verifiedText") is not None)`, `verified_text=output.get(
   "verifiedText")`, `verify_issues=output.get("verifyIssues")`.
   - pattern: existing field mapping in `_build_summary`.
   - approach: test-after.
   - acceptance (EARS): WHEN the summarize job persists a row whose output was verified, THE SYSTEM
     SHALL store `verified_text` and `verify_issues` on the Summary.

7. MODIFY `backend/app/api/documents.py` `resummarize` (:510-517) - copy `verified`/`verified_text`/
   `verify_issues` from the fresh output onto the Summary (Re-summarize verifies by default).
   - pattern: the field-copy block at :510-517.
   - approach: test-after.
   - acceptance (EARS): WHEN a single summary is re-summarized, THE SYSTEM SHALL run verify and store
     the verified fields.

8. MODIFY `backend/app/services/bundles.py` `bundle_summary_entries` - pass `verify=False` into its
   summarize_row call so the bundle export path does not verify.
   - pattern: its existing summarize_row invocation (confirm exact line during build).
   - approach: code.
   - acceptance (EARS): WHEN a category bundle is summarized, THE SYSTEM SHALL NOT run the verify
     pass.

9. MODIFY `frontend/components/review/summaries-view.tsx` (+ the summary type near
   `hooks/use-summaries`) - render a small "AI-fixed" flag on a card when `verifyChanged` is true;
   mirror the existing `manualCheck` badge styling.
   - pattern: the `manualCheck` badge in summaries-view.tsx.
   - approach: test-after.
   - acceptance (EARS): WHEN a summary has `verifyChanged=true`, THE SYSTEM SHALL show the AI-fixed
     flag on its card; WHEN false, THE SYSTEM SHALL NOT.

10. Tests (satisfy SonarCloud new-code coverage): `backend/tests/test_summary_verify.py` (verify_summary
    happy/none/failure), extend a summarize_engine test for the `verify` param wiring, a
    `test_models_methods` case for `effective_text` precedence; a frontend test for the flag.
    - approach: tdd/test-after per task above.

## Validation loop
- Backend lint/format: `docker compose exec -T api sh -c 'cd /app && uv run ruff check . && uv run ruff format --check .'`
- Backend tests: `docker compose exec -T api sh -c 'cd /app && uv run pytest -q'`
- Migration: `docker compose exec -T api sh -c 'cd /app && uv run alembic upgrade head'`
- Frontend: `cd frontend && pnpm typecheck && pnpm test`
- Manual (synthetic doc only): summarize a small synthetic record; confirm `GET /{id}/summaries`
  returns `verified=true`, and a card with an injected fabrication shows the AI-fixed flag.

## Risk / rollback
- Blast radius: summary generation only (segmentation/classification/title untouched). +1 LLM call
  per summarized row on every run (cost/latency ~2x the summary calls; noted + accepted).
- Rollback: set `SUMMARY_VERIFY=false` (env) to disable the pass with no redeploy; revert the commit
  to remove the code; migration is additive (downgrade drops the three columns).
