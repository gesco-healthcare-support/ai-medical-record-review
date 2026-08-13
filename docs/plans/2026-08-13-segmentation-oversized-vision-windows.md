---
feature: Bound segmentation vision windows so byte-light documents stop failing
date: 2026-08-13
status: in-progress
base-branch: main
related-issues: []
---

## Goal

No segmentation vision call carries an unbounded number of pages, so a byte-light document
segments successfully instead of failing every attempt on a server-side deadline.

## Context & decisions

Document 68cb2500 (241 pages) failed segmentation 6/6 times. Measured across all 30 documents
that have segment jobs, failure tracks **max pages per window**, not page count:

| doc      | pages | windows | max pages/window | segment errors |
| -------- | ----- | ------- | ---------------- | -------------- |
| 68cb2500 | 241   | 1       | 241              | 6/6            |
| 67eb1486 | 461   | 3       | 378              | 4/6            |
| 2ac86a24 | 311   | 2       | 295              | 1/2            |
| ec0950a9 | 217   | 2       | 180              | 2/3            |
| 9ea9c2b7 | 229   | 2       | 188              | 1/2            |
| 186300eb | 182   | 1       | 182              | 0/3            |
| ee5ce4f9 | 793   | 26      | 93               | 0/1            |
| d9fa2fd0 | 2673  | 48      | 191              | 0/1            |

`byte_budgeted_windows` (windows.py:46) packs pages until `window_budget_mb` (12.5) and caps
nothing else, so a byte-LIGHT PDF (68cb2500 is ~52KB/page) puts a huge page count into one call.
Image-heavy records split into many small windows and never failed.

### Measured 2026-08-13 (task 2, on the box, document 68cb2500)

| pages | payload | seconds                       | input tokens | thinking tokens |
| ----- | ------- | ----------------------------- | ------------ | --------------- |
| 20    | 0.9 MB  | 11.6                          | 6,364        | 1,138           |
| 40    | 1.7 MB  | (429 rate-limited, no timing) | -            | -               |
| 80    | 3.5 MB  | 20.2                          | 21,844       | 2,799           |
| 120   | 5.2 MB  | 39.9                          | 32,164       | 4,927           |
| 160   | 7.2 MB  | 54.5                          | 42,484       | 6,908           |
| 200   | 9.0 MB  | 106.3                         | 52,804       | 6,055           |
| 241   | 11.1 MB | **179.0**                     | 63,382       | 12,002          |

Root cause confirmed: the 241-page window needs 179s against a 120s deadline, so it can never
complete. Two claims from the pre-measurement draft are now DISPROVEN and must not be repeated:

1. **Not a token explosion.** The draft claimed ~1290 tokens/page and ~311k tokens. Measured is
   63,382 input tokens for 241 pages, about **263/page** - so `tokens.py:32`'s estimate of 259 is
   accurate and there is NO 5x pacer undercount. The 1290 figure applies to IMAGE parts (the
   summary multimodal path), not to inline PDF pages. The "fix the token undercount" follow-up is
   withdrawn as unfounded.
2. **A deadline above 120s IS honoured.** Previously unverified; the 179s call completed under a
   600s client deadline. Raising the deadline is therefore an available lever.

### Build outcomes (2026-08-13)

- **`window_max_pages` shipped at 100, not the 160 the measurement rule produced.** Adrian's call,
  and the better one: 100 is a round number clearly below the ~180 failure onset instead of sitting
  near it, and the mechanism he asked for - "12.5 MB or 100 pages, whichever comes first" - is
  exactly what the code does. Deriving an exact boundary from the curve was effort spent on
  precision that did not change the outcome. Cost accepted: more windows per record, so more calls
  and more seams.
- **`genai_http_timeout_ms` NOT raised.** Left at 120000; see task 6's outcome.
- **Task 7 (recall A/B) is a FOLLOW-UP, not a ship gate.** Adrian's call, on the reasonable ground
  that the tester is blocked now and the cap is well-supported by production error data. The
  measurement that motivated the gate is recorded here so the follow-up has a starting point:
  comparing windows with the cap against without it across the 11 labelled cases, 3 change. At the
  shipped cap of 100 more will change than at 160, so the A/B is worth running.

  | case          | pages | max window uncapped | at cap 160 | windows   |
  | ------------- | ----- | ------------------- | ---------- | --------- |
  | Case 3        | 227   | 183                 | 160        | 2 -> 2    |
  | Ancheta       | 461   | 378                 | 160        | 3 -> 5    |
  | Manual Case 2 | 182   | 182                 | 160        | 1 -> 2    |
  | other 8       | -     | unchanged           | unchanged  | unchanged |

  Manual Case 2 is the one to watch: it worked as a SINGLE window (0/3 errors) and now gains a seam,
  and seams are where over-segmentation shows up. Ancheta has no useful baseline - its 378-page
  window already fails 4/6 in production.

This supersedes `docs/plans/2026-08-13-vertex-capacity-and-segmentation-deadline.md`, which
attributed the failure to DENSE pages. That was exactly backwards and the file is deleted in
task 1 so it cannot mislead later.

Mechanism, proven separately: google-genai forwards `HttpOptions.timeout` to Vertex as the
SERVER-side deadline. A client timeout of 8000ms produced a server `504 DEADLINE_EXCEEDED` at
6.2s. Retrying is therefore futile - the same deadline binds every attempt, and job 1000174 took
eight identical 504s over 17.5 minutes before reporting anything.

Resolved decisions:

- **Decision: measure the duration/page-count curve before choosing the bound**, because the
  earlier value (300s) was a guess and the earlier cause was wrong; a number from a curve is
  defensible and a guess is not.
- **Decision: the cap is a new setting rather than a lowered `window_budget_mb`**, because
  shrinking the byte budget would also re-split image-heavy documents that currently work fine,
  changing output for records with no problem.
- **Decision: a deadline 504 stops being retried** (agreed before the research pass and not
  reversed), because it is deterministic and eight identical failures only delay the report.
- **Decision: already-segmented documents are NOT silently re-run.** Changing window boundaries
  can change rows, so re-segmentation is a per-document human choice.
- **Decision: whether to also raise `genai_http_timeout_ms` follows the measurement**, and only
  if the measurement shows Vertex honours a deadline above 120s - currently unverified.
- **Decision: the `tokens.py` PDF-page undercount is out of scope** (separate follow-up).

## All needed context

Anchors verified at `9dbe735`:

- `backend/app/services/windows.py:46` `byte_budgeted_windows(pdf_path, n, overlap, budget_bytes)`
  - packs at `:62-66`, raises only for a single oversized page at `:57`.
- `backend/app/services/segment_engine.py:158` calls it with `settings.window_overlap` and
  `int(settings.window_budget_mb * 1024 * 1024)`.
- `backend/app/services/segment_engine.py:142` `run_segmentation(pdf_path, total_pages,
progress=None, page_text_fn=None)` - DB-free; the measurement seam.
- `backend/app/services/segment_engine.py:53` `_window_rows(pdf_path, window_start, window_end,
client)` - builds a sub-PDF and sends it as ONE inline `types.Part`.
- `backend/app/services/segment_engine.py:37` `_generation_config()` - temp 0, `response_schema`,
  `thinking_budget=settings.segment_thinking_budget`.
- `backend/app/config.py:220` `window_budget_mb: float = 12.5`, `:221` `window_overlap: int = 30`,
  `:158` `segment_thinking_budget: int = -1`, `:131` `genai_http_timeout_ms: int = 120000`,
  `:124` `genai_max_retries: int = 8`, `:189` `segment_window_workers: int = 3`.
- `backend/app/services/genai_retry.py:141` - every `errors.ServerError` is retried, 504 included;
  loop at `:132`, `raise last` at `:168`.
- `backend/app/worker/failures.py:28` - `ServerError` returns `"transient"`. Its module docstring
  states the transient set MUST mirror the seam's retryable set, so both change together.
- `backend/app/errors.py:74` - any `ServerError` maps to `AI_BUSY_MESSAGE`.
- Pattern to mirror for a new setting reaching containers: the `GENAI_MAX_RETRIES` block in
  `docker-compose.yml` (explains WHY the key is named, fallback mirrors `app/config.py`).
- Pattern to mirror for a pure-function test: `backend/tests/test_failures.py` `_server_error`
  helper at `:31`.

Gotchas:

- `pytest` needs the DEV-stack Postgres on `127.0.0.1:5432` (`docker compose -f
docker-compose.dev.yml up -d postgres`), NOT 5433. Corrected 2026-08-13: `conftest.py:92` prefers
  5432 and `:120-147` actively REFUSES a DATABASE_URL on 5433, because that is the running
  application's database and the fixtures insert rows into it. With neither up the suite errors at
  fixture setup (36 errors observed 2026-08-12) rather than reporting real results; with dev
  Postgres up the same files run in 1.5s.
- Document 68cb2500 belongs to the alocker account. Adrian granted per-action permission for a
  read-only timing measurement only: read the PDF and call the seam directly, never enqueue a job,
  and return only timings, page counts and token counts. No record content leaves the box.
- Commit scope must come from `.claude/rules/commit-scopes.md`; `segmentation` fits.
- Server code is `a827941`, three commits behind main; #94 needs migration
  `c5d81f6a3b70_job_build_sha`. Keep the deploy minimal - do not drag #92-#94 along.

## Tasks

### 1. Delete the superseded plan

- **what:** DELETE `docs/plans/2026-08-13-vertex-capacity-and-segmentation-deadline.md`.
- **pattern:** n/a.
- **approach:** code
- **acceptance:** WHEN the repository is searched for the phrase "dense" as a segmentation cause,
  THE SYSTEM SHALL return no tracked plan file asserting it.

### 2. Measure duration against pages per window

- **what:** CREATE `backend/scripts/eval/window_duration_curve.py`. For document 68cb2500 on the
  box, time `_window_rows` for sub-windows of increasing page count (suggested ladder: 20, 40, 80,
  120, 160, 200, 241 pages starting at page 1), each with `HttpOptions.timeout` set high enough not
  to bind (600000ms). Record per run: pages, wall-clock seconds, `usage_metadata` prompt/candidate/
  thinking token counts when present, and outcome. Print an aggregate table only. Also record
  whether a 600000ms deadline is accepted at all, which answers the currently-unverified question
  of whether Vertex honours a deadline above 120s.
- **pattern:** mirror `segment_engine._window_rows` payload construction (`:55-61`) and reuse its
  `_generation_config()` and `SEGMENTATION_PROMPT`, so the request measured is the request
  production sends.
- **DEVIATION from the drafted plan (2026-08-13):** the script calls
  `client.models.generate_content` directly instead of calling `_window_rows`, and builds its own
  client instead of `get_genai_client()`. Two reasons, both fatal to the measurement otherwise:
  `_window_rows` goes through `generate_with_retry`, whose exponential backoff and cross-process
  Redis pacer add wall-clock that is not model time and would confound the exact number being
  measured; and `get_genai_client()` is `lru_cache`d and takes its deadline from
  `genai_http_timeout_ms`, the value under investigation. Verified that the deployed code at
  `a827941` has a `_generation_config()` byte-identical to main's, so the box's measurement is
  valid for main.
- **approach:** code
- **acceptance:** WHEN the script completes, THE SYSTEM SHALL print, for each page count in the
  ladder, the wall-clock duration and outcome, and SHALL print no medical record text.

### 3. Cap pages per window

- **what:** MODIFY `backend/app/config.py` to add `window_max_pages: int` next to
  `window_budget_mb` (`:220`). MODIFY `backend/app/services/windows.py:46`
  `byte_budgeted_windows` to take a `max_pages` argument and stop packing when either the byte
  budget OR `max_pages` is reached. MODIFY `backend/app/services/segment_engine.py:158` to pass
  `settings.window_max_pages`.
  Set the default by this RULE, not by guesswork: the largest ladder page count from task 2 whose
  measured duration is at most 50% of `genai_http_timeout_ms`, rounded down to a multiple of 10.
  The 50% margin exists because the measurement is single-window and production runs
  `segment_window_workers = 3` concurrently.
- **pattern:** the existing packing loop at `windows.py:62-66`; keep the single-oversized-page
  guard at `:57` untouched.
- **approach:** tdd
- **acceptance:**
  - WHEN a PDF's pages are small enough that more than `window_max_pages` fit inside
    `window_budget_mb`, THE SYSTEM SHALL emit windows of at most `window_max_pages` pages.
  - WHEN the byte budget binds before the page cap, THE SYSTEM SHALL produce byte-identical
    windows to the current behaviour.
  - WHEN a single page exceeds the byte budget, THE SYSTEM SHALL still raise the existing
    RuntimeError naming the page and the required `WINDOW_BUDGET_MB`.
  - The system shall place every page of the document in at least one window.

### 4. Make the new key settable

- **what:** MODIFY `docker-compose.yml` to name `WINDOW_MAX_PAGES` in `x-backend-env` with a
  fallback mirroring `app/config.py`; MODIFY `.env.example` to advertise it.
- **pattern:** the `GENAI_MAX_RETRIES` block in `docker-compose.yml` - state WHY the key is named.
- **approach:** code
- **acceptance:** WHEN `WINDOW_MAX_PAGES` is set in `.env`, THE SYSTEM SHALL report that value
  from inside the container via `docker compose exec -T api printenv WINDOW_MAX_PAGES`.

### 5. Stop retrying a deterministic deadline 504

- **what:**
  - MODIFY `backend/app/errors.py`: add `is_deadline_exceeded(exc)` returning
    `getattr(exc, "code", None) == 504` (structural, no google.genai import so the module stays
    light), and `AI_DEADLINE_MESSAGE`. Route it in `genai_user_message` at `:74`.
  - MODIFY `backend/app/services/genai_retry.py:141`: re-raise immediately when
    `is_deadline_exceeded(exc)`; every other `ServerError` keeps riding out the budget.
  - MODIFY `backend/app/worker/failures.py:28`: return `"permanent"` for a deadline 504.
- **pattern:** the existing PerDay carve-out at `genai_retry.py:151-152` - same shape, re-raise
  inside the except branch after recording the metric.
- **approach:** tdd
- **acceptance:**
  - WHEN the seam receives a 504, THE SYSTEM SHALL call the model exactly once and re-raise
    without sleeping.
  - WHEN the seam receives a 503, THE SYSTEM SHALL attempt `genai_max_retries` calls.
  - WHEN a job fails on a deadline 504, THE SYSTEM SHALL classify it `permanent` and render
    `AI_DEADLINE_MESSAGE`, and SHALL NOT render `AI_BUSY_MESSAGE`.

### 6. Raise the deadline only if measurement supports it

- **what:** MODIFY `backend/app/config.py:131` `genai_http_timeout_ms` ONLY IF task 2 showed a
  deadline above 120000 is honoured AND a capped window's measured duration exceeds 60000ms.
  Set it to twice the slowest capped-window duration, rounded up to a whole minute. If either
  condition fails, leave the value at 120000 and record in the plan why.
- **OUTCOME (2026-08-13): NOT changed; left at 120000.** The first condition held - a deadline above
  120s is honoured (the 179s call completed under 600s). The second did NOT: the capped window is
  160 pages at a measured 54.5s, under the 60000ms trigger. Raising it would have been a change
  without evidence. The `config.py:131` comment was rewritten instead, replacing the false claim
  "120s covers a large vision window" - it does not, an uncapped 241-page window needs 179s - with
  the actual reason 120s is now correct: the cap holds the slowest window at 54.5s. The lever stays
  documented and available if a capped window ever runs long.
- **pattern:** the comment convention at `config.py:128-131` - state the measurement and date.
- **approach:** code
- **acceptance:** WHEN `genai_http_timeout_ms` differs from 120000, THE SYSTEM SHALL carry a
  comment naming the measured duration that justifies the value.

### 7. Prove segmentation quality did not regress

- **what:** Run the boundary harness before and after the cap on the same labelled case and
  compare recall and strict doc-F1.
- **pattern:** `experiments/a1-segmentation/src/bake_off.py` (usage in its docstring at `:8`);
  `backend/scripts/eval/segmentation_boundary_ab.py` for the A/B shape.
- **approach:** test-after
- **acceptance:** WHEN the harness runs on a labelled case with the cap active, THE SYSTEM SHALL
  report recall no lower than the same case scored without the cap.

## Validation loop

```
cd backend && uv run ruff format --check . && uv run ruff check .
cd backend && uv run pytest -q
docker compose config --quiet
cd experiments/a1-segmentation && uv run python src/bake_off.py selftest
```

`pytest` requires the dev-stack Postgres on `127.0.0.1:5433` and Redis first; without them it
errors at fixture setup and is NOT a real result. `bake_off.py selftest` validates the search
logic with no Vertex spend; the labelled-case run in task 7 does cost spend and is run
deliberately, once.

Post-deploy on the box:

```
docker compose exec -T api printenv WINDOW_MAX_PAGES
```

Then ask the tester to re-run document 68cb2500 and confirm segmentation completes. Do not
re-run it from here - it is alocker's record and the timing measurement was the only granted
per-action permission.

## Risk / rollback

**Blast radius:** segmentation window boundaries for byte-LIGHT documents only. Documents whose
byte budget binds first - which is every image-heavy scan, including all the large records that
currently succeed - produce identical windows. The 504 change affects every genai caller, but only
for a status code that is currently guaranteed to fail after eight attempts anyway.

**Rollback:** set `WINDOW_MAX_PAGES` high enough to never bind (for example 10000) in `.env` and
recreate the workers - no deploy needed, which is the point of task 4. To revert the code, revert
the commit and recreate.
