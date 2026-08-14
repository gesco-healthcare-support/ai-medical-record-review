---
feature: Worker queue lanes are enumerated once at boot, so users who register later are stranded
date: 2026-08-13
status: draft
base-branch: main
related-issues: []
---

## Goal

A user who registers while the workers are running has their jobs picked up, instead of sitting in
`queued` forever with no error anywhere.

## Reported by

Levon, 2026-08-13, after being given access to the box. He diagnosed it independently, reproduced it
locally, and named the exact function. His report is accurate on the core defect; two details are
corrected below, and one hypothesis is disproven by the data.

## The defect, verified

`app/worker/__main__.py:34` `_user_ids()` selects every `User.id`. It is called ONCE at `:75`, the
lane list is built, and `:85` enters `RoundRobinWorker(...).work(...)`, which never returns. So the
set of queue lanes a worker serves is frozen at boot.

Jobs are routed per user: `app/worker/queues.py:60` `lane_name(base, user_id)` yields `segment:5`,
and `:69` `queue_for(kind, user_id)` enqueues onto it. Nothing validates that a listener exists for
that lane, so an unserved lane is a silent black hole - the job is enqueued successfully, RQ holds
it, and no worker ever pops it. No error, no timeout, no log line. It presents as "the AI is slow".

**Confirmed in production 2026-08-13:**

- `select id from "user"` -> `1 2 3 4 5`
- both segment and summarize workers: `listening round-robin on 5 queue(s): ['segment', 'segment:1',
'segment:2', 'segment:3', 'segment:4']`
- **user 5 has no listener.** That is Levon, who registered after the 2026-08-13 deploy restarted the
  workers. Any job he submits routes to `segment:5` / `summarize:5` and hangs.
- No RQ lane currently holds waiting jobs and no job is in `queued`, so nothing is lost yet - he had
  not submitted work at the time of checking.

## Corrections to the report

1. **`recover_orphans` is not what marks a restarted job `interrupted`.** It is called only from
   `app/main.py:30`, which is API startup - restarting workers does not run it. What actually
   finalizes an abandoned work-horse is the RQ failure callback `on_job_failed`
   (`app/worker/finalizers.py:84`), wired at `app/services/jobs.py:235` and
   `app/worker/tasks.py:172`. The operational advice is still right - restart when quiet - but the
   distinction matters: restarting workers alone will NOT reap pre-existing orphans. That needs an
   API restart.
2. **There is a worse variant of the same bug.** `_user_ids()` fails SOFT: on any DB exception it
   logs a warning and returns `[]`, so the worker boots serving base queues only. A database blip
   during a deploy therefore strands EVERY user's per-user jobs, not just newly registered ones, with
   the same total silence. The soft failure is deliberate and defensible on its own terms (a worker
   that refuses to boot is worse), but combined with silent lane routing it converts a transient DB
   error into an indefinite, invisible outage.

## Disproven: the `interrupted` hypothesis

Levon wondered whether the 18 interrupted segment attempts were people restarting workers over
running jobs. Plausible mechanism, but the timestamps say otherwise:

| when             | count | what it actually is                                                                            |
| ---------------- | ----- | ---------------------------------------------------------------------------------------------- |
| 2026-07-06 03:07 | 12    | one bulk event; ids 1000001-1000052 are the offset ids from the local -> server data migration |
| 2026-07-21       | 2     | jobs 4 and 5, both frozen at `verifying 27/32` - the known Tesseract/OpenMP deadlock           |
| Jul 17 / 20 / 22 | 4     | scattered singles                                                                              |
| 2026-08-13       | 0     | none from today's three worker restarts                                                        |

So restarts are not the main contributor. Today's deploys produced zero interrupted jobs, because
each one pre-flighted on `0 active jobs`.

## Immediate mitigation

Restart the segment and summarize workers while nothing is running. They re-enumerate and pick up
lane 5. This is safe RIGHT NOW: zero jobs in `queued`, `running` or `paused`.

```
cd /home/adityag/mrr && docker compose restart segment-worker summarize-worker
```

Verify with `docker compose logs segment-worker | grep "listening round-robin" | tail -1` - it must
show `segment:5`.

This is a mitigation, not a fix. It recurs on the next registration.

**APPLIED 2026-08-13.** Adrian's call was to restart the workers AND the API - the complete form of
the operation, since only an API restart runs `recover_orphans`. Pre-flighted on `0 active jobs`, so
nothing in flight was lost. After: both worker types report `listening round-robin on 6 queue(s)`
including `segment:5` / `summarize:5`, the API logged no orphans to reap (nothing was stuck),
`GET /` 200, 0 errors. User 5 is now served.

The next person who registers will be stranded again until someone notices, which is the whole
argument for doing one of the real fixes below rather than leaving this as a runbook step.

## Options for the real fix - Adrian's call, none chosen yet

1. **Shard the lanes** (`user_id % N`, Levon's second suggestion). A fixed lane set every worker
   always covers, so registration never changes the topology and there is nothing to refresh. Loses
   strict per-user isolation - users sharing a shard queue behind each other - but the lanes exist
   for fairness, not isolation, and approximate fairness is what fairness in a shared queue means
   anyway. Simplest to reason about and the only option with no refresh race.
2. **Re-enumerate periodically.** Keeps exact per-user lanes. Needs a custom work loop, since
   `Worker.work()` does not return; and it leaves a window between registration and refresh, so it
   narrows the bug rather than removing it.
3. **Refuse to enqueue onto an unserved lane.** Belt-and-braces regardless of 1 or 2: at enqueue
   time, check the lane has a listener and fail loudly (or fall back to the base queue) instead of
   silently accepting work nobody will do. Attacks the SILENCE, which is the part that made this
   cost a day of confusion rather than a minute.
4. **Fix the soft-fail blast radius.** If `_user_ids()` returns `[]` because of a DB error, that is
   different from "no users exist" and should at minimum log at ERROR and expose a health signal.

Recommendation if asked: 1 + 3. Sharding removes the class of bug; the enqueue guard means any
future routing mistake is loud instead of invisible.

## Not yet decided

- Whether to backfill/re-run anything for user 5 (nothing is currently stuck, so probably nothing).
- Whether the base queue should remain in the lane list once sharded.
- Levon says more findings from an end-to-end synthetic run are coming separately; hold design until
  those land in case they overlap.
