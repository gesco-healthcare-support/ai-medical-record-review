---
feature: Open threads as of 2026-08-13, parked to answer a segmentation question
date: 2026-08-13
status: draft
base-branch: main
related-issues: []
---

## Why this file exists

Work was parked mid-flight to answer a question from Adrian's boss about segmentation. This is the
state to resume from. Three threads are open; none of them blocks the tester.

## Shipped and DEPLOYED today - do not redo

| PR  | what                                                                                             | state            |
| --- | ------------------------------------------------------------------------------------------------ | ---------------- |
| #95 | named SUMMARY_MODEL, GENAI_HTTP_TIMEOUT_MS, SEGMENT_THINKING_BUDGET, WINDOW_BUDGET_MB in compose | merged, deployed |
| #96 | `window_max_pages=100` page cap + deadline-504 fast-fail + `AI_DEADLINE_MESSAGE`                 | merged, deployed |
| #97 | nine more settings named in compose + derived passthrough guard test                             | merged, deployed |

Box is on main `1cfcbb2`, image `build_sha=93dc0b9` (#97 was compose-only, no rebuild), alembic
`c5d81f6a3b70`. Verified inside the workers: `summary_model=gemini-3.5-flash`,
`window_max_pages=100`, `genai_model=gemini-2.5-flash`.

`SUMMARY_MODEL=gemini-3.5-flash` is set in the box `.env` as an INTERIM override; `app/config.py`
still defaults to `gemini-2.5-pro`. Backup of the pre-deploy DB at
`~/mrr-predeploy-20260813-183325/mrr.dump`.

## Thread 1 - queue lane staleness (mitigated, fix undecided)

Full detail: `docs/plans/2026-08-13-worker-queue-lane-staleness.md`. Reported by Levon, verified,
mitigated by restarting workers + API. User 5 is now served. It RECURS on the next registration.
Recommendation on the table: shard lanes by `user_id % N` plus an enqueue-time guard that refuses or
falls back when a lane has no listener. Nothing started. Levon has more findings coming from an
end-to-end synthetic run - wait for those before designing.

## Thread 2 - summary model is unscored (quality debt)

Full detail: `docs/plans/2026-08-13-summary-model-availability-and-early-giveup.md`.
`gemini-3.5-flash` is writing real medical summaries without having been scored against the frozen
human baselines, because Vertex refuses `gemini-2.5-pro` for this project (0/8 on the configured
endpoint). Adrian accepted this knowingly to unblock the tester. `jobs.model` stamps every job, so
interim output is identifiable later. The plan's task 2 (per-job early give-up so a refused model
fails in minutes rather than 96) is also unstarted.

## Thread 3 - pacer research (findings below are the only record)

Adrian chose "research properly first, no code". Nothing is written elsewhere, so these numbers live
here.

Production counters (`scripts/eval/vertex_stats.py` on the box, cumulative):

| model                 | attempts  | accepted | 429     | reject %  | pacer wait  |
| --------------------- | --------- | -------- | ------- | --------- | ----------- |
| gemini-2.5-flash      | 710       | 630      | 80      | 11.3%     | 24,042s     |
| gemini-2.5-pro        | 330       | 259      | 71      | 21.5%     | 12,495s     |
| gemini-2.5-flash-lite | 4         | 4        | 0       | 0%        | 7s          |
| **total**             | **1,044** | **893**  | **151** | **14.5%** | **36,544s** |

36,544s is 10.2 hours of cumulative wait (summed across ~6 workers, so wall-clock is less) to avoid a
14.5% rejection rate - about 41s of pacer wait per accepted call.

Mechanism, from `app/services/llm/pacing.py`:

- `_DECREASE_FACTOR = 0.5`, `_DECREASE_COOLDOWN_S = 2.0`, `_MIN_RATE_FRACTION = 0.02`,
  `_INCREASE_FRACTION = 0.02`.
- Descent: floor is 2% of ceiling, so 20 -> 0.4 rpm is ~6 halvings; at one halving per 2s the rate
  reaches the floor after roughly 12 SECONDS of sustained rejections. The cooldown guards a burst
  from one event, not a persistent condition.
- Recovery: +2% of ceiling per SUCCESS = 49 successes floor-to-ceiling, but at the floor you earn
  0.4 successes/minute, so ~2 HOURS - and recovery is gated by the very rate that was throttled.
- Inversion: `acquire()` gives up after `MAX_ACQUIRE_WAIT_S` (300s) and the caller proceeds anyway
  (`pacing.py:298`). Under sustained pressure you pay the latency and get no protection, and the
  resulting flood produces more rejections.

Observed live: a local Case 3 run hit four 300s stalls (20 of 45 minutes) with the rate at 3.1 of
20 rpm, which killed the first A/B attempt. After deleting the `llm:pace:*` keys the same run had
zero stalls for most of its length, then one stall reappeared after ~50 calls.

**The gap that blocks a real answer:** `genai_metrics` keeps cumulative counters only. The rate is
read but never recorded, so there is NO time series and "how often is the rate at floor during a real
job" cannot be answered from existing data. Closing that needs a rate sample emitted over time -
instrumentation, not a fix. Do that before changing any constant.

## Segmentation A/B result (done, for the record)

Case 3, 227 pages, 51 gold sub-documents, production code, cap 100:
boundary_recall **1.000**, mean_offset **0.000** (51/51 exact), gap_pages **0**, overlap_pages **0**,
precision 0.680, over_seg_ratio 1.490, exact_doc_f1 0.598, 2,506s.

Matches the documented Case 3 baseline ("51/51 exact", "recall is already 1.00"). The 1.49x
over-segmentation is the known pre-existing characteristic, by design - the architecture over-splits
recall-first and merges in the verify pass. The UNCAPPED control could not be produced: it failed
with a 504 after 165s, which is the bug reproducing on a ground-truth document.

Harness: `backend/scripts/eval/segmentation_cap_ab.py` (uncommitted). Needs
`--extra classifier`, `--env-file ../.env`, and a second env file overriding
`GOOGLE_APPLICATION_CREDENTIALS` to the HOST path `C:/src/mrr-ai/secrets/vertex-sa.json` (the repo
value is the in-container path) plus any parseable `DATABASE_URL`.

## Uncommitted / environment notes

- `backend/scripts/eval/segmentation_cap_ab.py` is NOT committed.
- Local dev containers started this session: `mrr-ai-postgres-1` (5432) and `mrr-ai-redis-1` (6379),
  both throwaway test services. The Patient Portal stack went down mid-session, which is what made
  the suite fail en masse - `pytest` had been silently using the Portal's Redis on 6379.
- Local pacer state was flushed (`llm:pace:*` deleted from `mrr-ai-redis-1`).
