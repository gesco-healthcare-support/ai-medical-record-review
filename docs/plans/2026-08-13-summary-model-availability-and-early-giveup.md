---
feature: Replace the unavailable summary model on evidence, and stop a refused model burning a whole job
date: 2026-08-13
status: draft
base-branch: main
related-issues: []
---

## Goal

Summarization runs on a model Vertex will actually admit, chosen on measured quality rather than
availability alone, and a model that is refusing everything ends its job in minutes instead of
grinding for an hour and a half.

## Context & decisions

Vertex stopped admitting `gemini-2.5-pro` for project `gen-lang-client-0785241985`. Measured live,
8 attempts per location on a trivial prompt with no load:

| endpoint            | accepted |
| ------------------- | -------- |
| global (configured) | 0/8      |
| us-east5            | 0/8      |
| us-central1         | 1/8      |
| us-west1            | 1/8      |
| europe-west4        | 2/8      |
| us-east1            | 3/8      |

Rejections return in ~0.1s, so the request never reaches a model. `gemini-2.5-flash` and
`flash-lite` were 5/5 everywhere in the same minutes on the same credentials, and
`gemini-3.5-flash` was 5/5 at 1.2s and accepted `thinking_budget` of both -1 and 0. So this is
model-specific and external: not our code, not the region, not auth or billing.

Secondary defect, and the reason this was invisible: job 1000173 spent 96 minutes retrying 429s,
then died as `rq.timeouts.JobTimeoutException` because the size-aware RQ cap for a 289-page record
is 289 x 20s = 5780s. That exception is unclassified, so the reviewer saw
`GENERIC_USER_MESSAGE` after an hour and a half.

Resolved decisions:

- **Decision: `gemini-3.5-flash` is the candidate**, because it is the only measured-available
  model that is a newer generation than 2.5-pro, so it does not revert the deliberate 07-28 move
  off `gemini-2.5-flash` for summary quality.
- **Decision: the code default stays `gemini-2.5-pro` until scoring clears**, because availability
  is not a quality argument and an unmeasured model must not become the permanent default for
  medical summaries.
- **Decision: the box runs 3.5-flash NOW via `SUMMARY_MODEL` in the server `.env`**, because the
  tester is fully blocked; the `Job` row stamps the model, so interim summaries stay identifiable.
  Adrian applies this himself - a protect-secrets hook blocks agent access to `.env`, and
  production env is his.
- **Decision: pass bar is "no worse than 2.5-pro on the same frozen human baselines".**
- **Decision: the control is an ABSOLUTE read against the frozen human baselines, PLUS a
  best-effort same-day pro arm via us-east1** (3/8 admission). If the pro arm cannot complete,
  report the absolute number and state the pro comparison was unobtainable. Do NOT fall back to
  `conformance_current_build.json`: it is dated 2026-07-31, does not record which model produced
  it, and is confounded by PR #61 audit style enforcement plus the tier-3 title/DOI and deposition
  changes that landed since.
- **Decision: early give-up is a per-job consecutive-failure counter in the summarize row loop.**
  NOT the pacer, because `pacing.py:152` deliberately absorbs rejection bursts behind a cooldown
  and keeps GLOBAL per-model state, so it cannot tell "this job is doomed" from "the fleet is
  busy", and one job could abort because another depressed the rate. NOT the seam, because
  segmentation is non-resumable while summarize pauses and resumes, so a seam-level abort would
  mean different things per caller.
- **Decision: a fallback model chain is NOT built now.** A static default plus a live
  `SUMMARY_MODEL` knob covers this event; a chain is speculative until a second event needs it.

## All needed context

Anchors verified at `9dbe735`:

- `backend/app/config.py:247` `self.summary_model = self.summary_model or "gemini-2.5-pro"` - the
  one line the gate in task 3 changes.
- `backend/app/config.py:78` `summary_thinking_budget: int = -1`. Verified live that 3.5-flash
  accepts -1, so no change needed. Do NOT also set a thinking LEVEL on a 3.x model: budget and
  level together are a 400.
- `backend/app/api/documents.py:778`, `:917`, `:1187` and `backend/app/api/admin.py:206` -
  `payload.model or get_settings().summary_model`. This is why `SUMMARY_MODEL` is the effective
  knob and `SUMMARY_BODY_MODEL` is not: the API stamps the resolved model onto the Job, and
  `summarize_engine.py:561`'s `model or model_for("body")` fallback never fires in production
  because `tasks.py` passes `job.model`.
- `backend/app/worker/tasks.py:590` `summarize_document(job_id)`; row pool at `:669`
  (`ThreadPoolExecutor(max_workers=settings.pipeline_workers)`); drain at `:687`; failure split at
  `:692` (`if classify_failure(exc) == "transient"`); `JobPaused` raised at `:743`;
  `JobNeedsAttention` at `:746`.
- `backend/app/worker/failures.py:20` `classify_failure`, `:46` `JobPaused`, `:78`
  `JobNeedsAttention(message, rows)`.
- `backend/app/errors.py:16` `AI_BUSY_MESSAGE`, `:19` `AI_DAILY_QUOTA_MESSAGE`.
- `backend/app/config.py:118-119` `job_timeout=3600`, `job_timeout_per_page=20.0` - the 5780s cap
  that the give-up must fire well inside of.

Scoring harness (verified present, outside this repo):

- `W:/MRR_Research_and_Analysis/90_Scripts/12_local_sample_run.py` - re-summarizes a deterministic
  sample of Adrian's LOCAL rows by calling `summarize_row` directly. No job is created and no
  stored `Summary` is touched. Sample: all category-3 and category-14 rows carrying stored OCR,
  plus the first 30 category-1 rows, ordered by `(document_id, start)`. Writes `/tmp/<label>.csv`
  inside the container. Takes the label as `sys.argv[1]`; the MODEL comes from settings, so the
  arm is selected by injecting the env var for that exec only.
- `W:/MRR_Research_and_Analysis/90_Scripts/11_score_app_baseline.py` - usage
  `python 11_score_app_baseline.py <app_summaries.csv> <label>`. Scores against the frozen human
  baselines. Strips the DOI prefix and the "(Pages X-Y)" suffix so lengths are like-for-like, and
  scores BOTH raw `text` and effective (`verified_text` where the audit rewrote, else `text`).
- `W:/MRR_Research_and_Analysis/02_Extracted_Data/human_baselines.json` - 11 categories.

Gotchas:

- The local harness needs `--extra classifier` and `127.0.0.1:5433/mrr_local_only` with
  `connect_timeout`, or categorization silently degrades.
- `pytest` needs the dev-stack Postgres on `127.0.0.1:5433` AND Redis, or it errors at fixture
  setup (36 errors observed) rather than reporting real results.
- Inject the arm's model with `docker compose exec -e SUMMARY_MODEL=... -T api python ...` rather
  than editing `.env`: it scopes the change to one command and avoids touching a secrets file.
- `GOOGLE_CLOUD_LOCATION` is already passed by compose, so the us-east1 pro attempt needs no code
  change.
- Commit scope from `.claude/rules/commit-scopes.md`: `summarize` fits.

## Tasks

### 1. Generate the candidate arm and score it

- **what:** Run the sample harness twice on the LOCAL stack and score both:
  `docker compose exec -e SUMMARY_MODEL=gemini-3.5-flash -T api python /w/.../12_local_sample_run.py flash35`
  then copy `/tmp/flash35.csv` out and run
  `python 11_score_app_baseline.py flash35.csv flash35`.
  Then attempt the pro control:
  `docker compose exec -e SUMMARY_MODEL=gemini-2.5-pro -e GOOGLE_CLOUD_LOCATION=us-east1 -T api python /w/.../12_local_sample_run.py pro25`
  and score it the same way. Record per-category scores for both arms plus how many pro rows
  actually completed.
- **pattern:** the existing before/after arm convention that produced
  `02_Extracted_Data/app_summaries_before.csv` and `conformance_before.json`.
- **approach:** code
- **acceptance:**
  - WHEN the candidate arm completes, THE SYSTEM SHALL produce a per-category score set for
    3.5-flash against the frozen human baselines.
  - IF fewer than 80% of the pro control rows complete, THEN THE SYSTEM SHALL report the pro
    comparison as unobtainable rather than presenting a partial arm as a control.
  - THE SYSTEM SHALL NOT reference `conformance_current_build.json` as the pro control.

### 2. Early give-up on a fully-refused model

- **what:** MODIFY `backend/app/config.py` to add `summarize_giveup_after_failures: int = 3` near
  `pipeline_workers` (`:107`). MODIFY `backend/app/worker/tasks.py` `summarize_document`: in the
  drain loop at `:687`, track this job's transient-failure count and its success count; when
  transient failures reach `summarize_giveup_after_failures` AND the success count is still zero,
  stop submitting and end the job with the genai message for the last exception instead of
  continuing. Zero successes is the discriminator - it is what 0/8 admission looks like, and it is
  what a transient blip with some rows succeeding does not.
- **pattern:** the existing terminal-signal shape at `tasks.py:746` `JobNeedsAttention(message,
rows)`, and `reason_for(exc)` in `failures.py:39` for the wording so a per-row reason and the
  job message agree.
- **approach:** tdd
- **acceptance:**
  - WHEN `summarize_giveup_after_failures` rows have failed transiently and no row has succeeded,
    THE SYSTEM SHALL end the job without submitting further rows.
  - WHILE at least one row has succeeded, THE SYSTEM SHALL NOT give up early regardless of the
    transient-failure count.
  - WHEN the job gives up early, THE SYSTEM SHALL record a user-facing message naming the AI
    service as unavailable, not `GENERIC_USER_MESSAGE`.
  - WHEN a model is refusing every call, THE SYSTEM SHALL surface the failure in under 10 minutes
    for a 289-page record, versus the 96 minutes observed on job 1000173.

### 3. Make the give-up threshold settable

- **what:** MODIFY `docker-compose.yml` to name `SUMMARIZE_GIVEUP_AFTER_FAILURES` in
  `x-backend-env` with a fallback mirroring `app/config.py`; MODIFY `.env.example` to advertise it.
- **pattern:** the `GENAI_MAX_RETRIES` block in `docker-compose.yml`.
- **approach:** code
- **acceptance:** WHEN `SUMMARIZE_GIVEUP_AFTER_FAILURES` is set in `.env`, THE SYSTEM SHALL report
  that value from `docker compose exec -T summarize-worker printenv SUMMARIZE_GIVEUP_AFTER_FAILURES`.

### 4. Change the code default only if the bar is met

- **what:** MODIFY `backend/app/config.py:247` to default `summary_model` to `gemini-3.5-flash`
  ONLY IF task 1 showed it no worse than the pro control on the frozen human baselines. If the pro
  control was unobtainable, present the absolute numbers to Adrian and get an explicit decision
  before touching this line - do not infer approval from availability.
- **pattern:** the comment convention at `config.py:244-247` - state the measurement and date, and
  why the previous choice changed.
- **approach:** code
- **acceptance:** WHEN `config.py:247` names a model other than `gemini-2.5-pro`, THE SYSTEM SHALL
  carry a comment citing the scoring run that justified it.

### 5. Retire the interim override

- **what:** Once task 4 lands and is deployed, Adrian removes `SUMMARY_MODEL` from the server
  `.env` so the box and the code default agree again.
- **pattern:** n/a - operational step, and his to run.
- **approach:** code
- **acceptance:** WHEN task 4 is deployed, THE SYSTEM SHALL resolve the same body model with and
  without the `.env` override present.

## Validation loop

```
cd backend && uv run ruff format --check . && uv run ruff check .
cd backend && uv run pytest -q
docker compose config --quiet
```

`pytest` requires the dev-stack Postgres on `127.0.0.1:5433` and Redis first; without them it
errors at fixture setup and is NOT a real result. Task 2 is the only behavioural change and is
`tdd`, so its tests must exist and fail before the implementation.

Post-deploy on the box:

```
docker compose exec -T summarize-worker printenv SUMMARY_MODEL SUMMARIZE_GIVEUP_AFTER_FAILURES
docker compose exec -T api python -c "from app.config import get_settings; print(get_settings().summary_model)"
```

## Risk / rollback

**Blast radius:** task 2 changes when a summarize job stops; a threshold set too low would abandon
a job that a brief 429 burst would have survived, which is why the zero-successes condition is
required rather than a bare count. Task 4 changes which model writes every summary body - the
largest quality-facing change here, which is why it is gated on task 1.

**Rollback:** the give-up is disabled by setting `SUMMARIZE_GIVEUP_AFTER_FAILURES` very high in
`.env` and recreating the workers - no deploy. The model reverts by setting `SUMMARY_MODEL` in
`.env`, also no deploy. Both knobs exist because PR #95 made them reach the containers.
