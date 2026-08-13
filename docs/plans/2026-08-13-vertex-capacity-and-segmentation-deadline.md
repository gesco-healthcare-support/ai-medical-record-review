# Vertex capacity + segmentation deadline

status: draft
date: 2026-08-13
branch: (not created yet)

All measurements below were taken live on the Sarhad box (192.168.100.58) on 2026-08-12 in server
UTC, during the incident.

## Context

The tester reported two failures. The DB shows eight failed jobs and ZERO successful jobs of any
kind on 2026-08-12. Two independent root causes; neither is the 2026-08-11 deploy (a827941, #91).

### Cause 1 - summarize: gemini-2.5-pro is not being admitted

Live probe, 8 attempts per location, trivial prompt, no load:

| endpoint            | accepted        |
| ------------------- | --------------- |
| global (configured) | 0/8             |
| us-east5            | 0/8             |
| us-central1         | 1/8             |
| us-west1            | 1/8 (avg 62.5s) |
| europe-west4        | 2/8             |
| us-east1            | 3/8             |

Rejections return in ~0.1s, so the request never reaches a model. `gemini-2.5-flash` and
`flash-lite` are 5/5 in every location tried, so this is model-specific, not auth, billing, project
or region. No Google incident is posted; this is dynamic-shared-quota contention, and the documented
mitigations are backoff, a regional fallback, or Provisioned Throughput.

`gemini-3.5-flash` measured 5/5 on global at 1.2s and accepts the seam's `thinking_budget=-1`
unmodified (verified live for both -1 and 0), so a swap needs no thinking-config change.

The visible failure is second-order: the retry loop grinds 429s until the size-aware RQ cap
(289 pages x 20s = 5780s) expires, then dies as `rq.timeouts.JobTimeoutException` - unclassified, so
it renders as GENERIC_USER_MESSAGE. Job 1000173 burned 96 minutes to produce that.

### Cause 2 - segment: our own 120s deadline returns as a server 504

`genai_http_timeout_ms` (120000) is forwarded by google-genai to Vertex as the SERVER-side deadline.
Proven: a client timeout of 8000ms produced a server `504 DEADLINE_EXCEEDED` at 6.2s. Job 1000174
took eight consecutive 504s ~2 min apart on `gemini-2.5-flash`, then exhausted
`genai_max_retries = 8` after 17.5 minutes.

Not a size ceiling - segment succeeded at 793, 498, 335, 300, 294 and 236 pages on the same 120s
deadline. The failing document (68cb2500, 241 pages) is dense, and segmentation keeps dynamic
thinking (`segment_thinking_budget = -1`), so a heavy window reasons past 120s. Retrying cannot
help: the deadline is constant across attempts.

Two stale comments to fix while here: config.py claims "120s covers a large vision window" (false)
and that bounding the timeout "turns a stall into an httpx timeout that generate_with_retry already
catches + retries" (wrong - it becomes a server 504).

### Cause 3 - the knobs that would have fixed this are inert

`SUMMARY_MODEL`, `GENAI_HTTP_TIMEOUT_MS`, `SEGMENT_THINKING_BUDGET` and `WINDOW_BUDGET_MB` are
absent from `docker-compose.yml`, so setting them in `.env` does nothing. Same class of bug the
compose file already documents for `VERTEX_MAX_RPM`, `PIPELINE_WORKERS` and `GENAI_MAX_RETRIES` -
this is the fourth occurrence.

GOTCHA worth recording: `SUMMARY_BODY_MODEL` IS passed by compose, which makes it look like a live
lever for the summary model. It is not. `documents.py:778` (and 917, 1187, and `admin.py:206`)
resolve the body model as `payload.model or get_settings().summary_model`, and the worker passes
`job.model` into `summarize_row`, so the `or settings.model_for("body")` fallback at
`summarize_engine.py:561` never fires. `SUMMARY_BODY_MODEL` reaches only the title and audit calls.

## Decisions (with Adrian, 2026-08-13)

- Unblock the tester first, separately from the code fix, so the fix is not rushed.
- Deadline value comes from MEASUREMENT, not a guess.
- Do not change the code default off 2.5-pro until 3.5-flash is scored against the frozen human
  baselines. Availability alone is not a reason to ship an unmeasured summarization model.
- Fix the compose bug class with a test, not four more lines.
- Add early give-up: no fix here is complete if a refusing model still burns a whole job budget.
- DEFERRED, not decided: whether to build a preferred-model-with-fallback chain. Revisit after the
  baseline scoring; a static default plus a live knob may be enough.

## Tasks

1. **Make the four dead keys live.** (`docker-compose.yml`, approach: code)
   Add `SUMMARY_MODEL`, `GENAI_HTTP_TIMEOUT_MS`, `SEGMENT_THINKING_BUDGET`, `WINDOW_BUDGET_MB` to
   the `x-backend-env` block with fallbacks mirroring `app/config.py`, in the existing house comment
   style (state WHY the key is named, per the surrounding blocks). Mirror them in `.env.example`.

2. **End the bug class.** (`backend/tests/test_config_compose_parity.py` NEW, approach: tdd)
   Assert every env-overridable `Settings` field is named in `docker-compose.yml`. Enumerate
   `Settings.model_fields`, upper-case each name, and require it appear as a key in the compose
   `x-backend-env` block. Deliberate exclusions go in an explicit, commented allowlist -
   `TESSERACT_CMD` is documented in compose as intentionally not passed, and DB/Redis/secret keys are
   set directly rather than passed through. The test must name the missing key in its failure
   message so the fix is obvious. This is what stops a fifth occurrence.

3. **Measure what a dense window actually needs.** (approach: code, read-only)
   Against document 68cb2500 on the server, run ONE segmentation window with a deliberately long
   deadline and record wall-clock; repeat for 2-3 windows to get a spread. Pick
   `genai_http_timeout_ms` from that number with headroom, and record the measurement in the config
   comment. Do NOT enqueue jobs on the tester's documents - read the PDF and call the seam directly.
   Constraint: this touches a real record, so it runs on the box and no page text comes back here.

4. **Stop retrying a deterministic 504.** (approach: tdd)
   - `app/errors.py`: add `is_deadline_exceeded(exc)` (structural, `code == 504`, no genai import)
     and a distinct `AI_DEADLINE_MESSAGE`. Route it in `genai_user_message`. Today a 504 renders as
     AI_BUSY_MESSAGE, which is actively misleading - it says the AI was busy when the limit was ours,
     and that wording is what sent this investigation at Vertex capacity first.
   - `app/services/genai_retry.py:141`: re-raise on a deadline 504 instead of counting it as a
     retryable 5xx. 503 must still ride out the full budget.
   - `app/worker/failures.py:28`: mirror it - a deadline 504 is "permanent". Its module docstring
     requires the transient set mirror the seam's retryable set, so both must change together.
   - Set `genai_http_timeout_ms` from task 3.

5. **Early give-up when a model is refusing everything.** (approach: tdd)
   A job must not spend its whole wall-clock budget discovering that a model is unavailable. After N
   consecutive refusals of the same model with zero successes in between, abort the job with the
   busy/quota message instead of continuing to retry per row. `services/llm/pacing` already records
   rejections and successes per model, so the signal exists; prefer reading it there over adding new
   state. Must not fire on a mixed pass (some rows succeeding) - the trigger is CONSECUTIVE failure
   with no success, which is what 0/8 admission looks like and what a transient blip does not.
   Target: a fully-refused model surfaces in single-digit minutes, not 96.

6. **Score 3.5-flash before changing the default.** (approach: code, gated)
   Run the summary scorer against the frozen human baselines for 3.5-flash, and compare with the
   13 successful 2.5-pro summaries already in the DB. Only if it holds up does
   `config.py:247` change from `gemini-2.5-pro`. Until then the default stays pro and the live
   `SUMMARY_MODEL` knob (task 1) is how the box runs 3.5-flash.

## Acceptance (EARS)

- WHEN the seam receives a 504 DEADLINE_EXCEEDED, THE SYSTEM SHALL raise on the first attempt with
  no backoff sleep.
- WHEN the seam receives a 503, THE SYSTEM SHALL retry up to `genai_max_retries`.
- WHEN a job fails on a deadline 504, THE SYSTEM SHALL classify it permanent and show
  AI_DEADLINE_MESSAGE, never AI_BUSY_MESSAGE.
- WHEN an env-overridable Settings field is not named in docker-compose.yml and not allowlisted,
  THE SYSTEM SHALL fail the test suite naming that key.
- WHEN a model returns N consecutive refusals with no interleaved success, THE SYSTEM SHALL end the
  job with the busy message rather than exhausting the job timeout.
- WHEN SUMMARY_MODEL is set in .env, THE SYSTEM SHALL use it for the summary BODY call (verified in
  the container, not merely in the file).
- WHEN no SUMMARY_MODEL is set, THE SYSTEM SHALL still resolve to gemini-2.5-pro until task 6
  passes.

## Validation loop

Backend + compose only; no Angular/UI in this diff.

    cd backend && uv run ruff format --check . && uv run ruff check .
    cd backend && uv run pytest -q
    docker compose config --quiet

NOTE: pytest needs the dev-stack Postgres on 127.0.0.1:5433 AND Redis; without them the suite
errors at fixture setup (36 errors, seen 2026-08-12) rather than reporting a real result. Bring
those up before treating a green run as meaningful.

Post-deploy on the box:

    docker compose exec -T summarize-worker sh -c 'env | grep -E "SUMMARY_MODEL|GENAI_HTTP_TIMEOUT_MS"'
    docker compose exec -T api python -c "from app.config import get_settings; print(get_settings().summary_model)"

Then have the tester re-run both failed documents - segment on 68cb2500 (241p) and summarize on
e1ea7871 (289p) - and confirm each completes. Do not re-run them from here: they belong to the
alocker account, which is hands-off without per-action permission.

## Open items

- Whether to add a preferred-model fallback chain (deferred above).
- Whether Provisioned Throughput is worth buying if 2.5-pro capacity does not return.
- `.env.example` and a tracked plan both cite `90_Scripts/` and `02_Extracted_Data/`, paths that
  exist only on Adrian's research drive. Unrelated to this incident, but it is a live rot risk in
  tracked files and two people have now had to ask whether the scorer they name is real.
