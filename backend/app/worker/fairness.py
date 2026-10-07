"""One reviewer's batch cannot hold every identify worker while another reviewer waits.

The per-user lanes (queues.py) and RoundRobinWorker give every lane a turn - but only when a worker
FREES. A job holds its worker until it finishes, so a reviewer who queues a batch can occupy every
identify worker, and the next reviewer waits for one of those jobs to end. On the live box,
2026-10-07: one reviewer queued 13 identifies in a minute, all three identify workers took them,
and a second reviewer's four identifies sat queued 6 to 12 minutes. Over the 30 days before, 34
identify jobs waited more than a minute while another reviewer held a worker, the longest about 20
minutes. Not one of those waits involved a 1000+ page record; every one was a batch.

So when an identify job is picked up and its reviewer already has ``identify_per_reviewer_cap``
identify jobs RUNNING, and some OTHER reviewer has an identify job WAITING, the job steps aside: it
goes back to the front of its own lane, and the worker's next round-robin turn serves the waiting
reviewer. A reviewer working alone is never capped - the rule only acts while someone else waits -
so with five workers and a cap of three, a lone reviewer still runs three at a time, as before.

Two guards make a livelock impossible, and both are load-bearing:

- Only a reviewer who is BELOW the cap counts as waiting. If every waiting reviewer is already at
  the cap, nobody steps aside, so a free worker always takes something.
- Only lanes THIS worker serves count. Lanes are enumerated once at worker start-up, so a reviewer
  added afterwards has a lane no running worker reads; their jobs wait until a restart. Stepping
  aside for them would hand the turn to nobody, and the busy reviewer would step aside forever.

Live state comes from Redis (the lane's queue length and RQ's StartedJobRegistry), not from the
``jobs`` table: a row can say ``queued`` for a job RQ has lost, and stepping aside for that would
also hand the turn to nobody. Every failure here answers "do not step aside" - the cost of a wrong
"no" is today's behaviour, the cost of a wrong "yes" is a job that never runs.
"""

import logging

from app.worker.queues import SEGMENT_QUEUE, base_queue_name, lane_name

logger = logging.getLogger(__name__)

# The lanes this worker process dequeues from, set by __main__ before work(). A forked work-horse
# inherits it. None (tests, a script calling a task directly) means the rule is off.
_served_lanes: tuple[str, ...] | None = None


def serve(lanes) -> None:
    """Record the lanes this worker listens on. Called once, at worker start-up."""
    global _served_lanes
    _served_lanes = tuple(lanes)


def _running(registry, exclude: str | None) -> int:
    """Distinct jobs running on one lane. RQ 2 keeps ``job_id:execution_id`` members and
    ``get_job_ids`` strips them back to job ids, so a job is counted once whatever its executions.
    ``cleanup=False`` keeps this a pure read; workers run the registry's own cleanup."""
    return len(set(registry.get_job_ids(cleanup=False)) - {exclude})


def should_step_aside(kind: str, owner, cap: int, redis, current_rq_id: str | None = None) -> bool:
    """True when this identify job should go back to its lane so a waiting reviewer goes first.

    ``current_rq_id`` is the RQ id of the job asking, which is already in its own lane's started
    registry while it runs and must not count against its own reviewer.
    """
    if cap <= 0 or not _served_lanes or owner in (None, ""):
        return False
    base = base_queue_name(kind)
    if base != SEGMENT_QUEUE:
        return False
    try:
        from rq import Queue
        from rq.registry import StartedJobRegistry

        own = lane_name(base, owner)
        if _running(StartedJobRegistry(own, connection=redis), current_rq_id) < cap:
            return False
        for name in _served_lanes:
            # The base queue holds ownerless jobs: no reviewer is waiting on it.
            if name in (own, base) or not name.startswith(f"{base}:"):
                continue
            if Queue(name, connection=redis).count == 0:
                continue
            if _running(StartedJobRegistry(name, connection=redis), None) < cap:
                return True
        return False
    except Exception:
        logger.warning("fairness check failed; running the job as picked", exc_info=True)
        return False
