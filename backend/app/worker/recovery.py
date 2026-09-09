"""Heartbeat-aware orphan recovery for the RQ pipeline.

Replaces the Flask job_queue's mark-ALL-queued/running-interrupted-at-boot sweep, which would kill
healthy in-flight jobs on a rolling restart with N workers. Instead we reconcile each DB job stuck
in queued/running against its RQ counterpart: a job whose RQ job is gone or in a terminal state
(its worker died) is marked interrupted; a job RQ still reports as
queued/started/deferred/scheduled has a live worker and is left alone.

Correlation is by the job's CURRENT RQ id, which is not always the DB id - a resumed summarize
run is dispatched under a fresh one - so `rq_job_id` is read in preference to it.

The transition itself goes through `services.jobs.mark_terminal`, the single writer for a
terminal outcome that is not the job's own success. That matters here specifically: this
function is named in that writer's docstring as one of the racers it serialises, so a
hand-written UPDATE from here can overwrite an outcome another party already committed.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job
from app.services.jobs import (
    ACTIVE_STATES,
    INTERRUPTIBLE_DOCUMENT_STATUSES,
    mark_terminal,
)

logger = logging.getLogger(__name__)

# RQ statuses that mean a worker is still on the job (or it is validly waiting to run).
_HEALTHY_RQ_STATUSES = frozenset({"queued", "started", "deferred", "scheduled"})


def recover_orphans(session: Session) -> int:
    """Interrupt DB jobs whose RQ counterpart is gone/terminal; leave healthy ones.

    Safe to call at web startup: it never touches a job a worker is still running, and never a
    job some other party has already finalized.

    Returns the number of jobs this call actually transitioned - not the number it wanted to.
    A job another writer finalized first is not counted, because it was not reaped by us, and
    every count returned is committed by the time it is returned.
    """
    from redis.exceptions import RedisError
    from rq.exceptions import NoSuchJobError
    from rq.job import Job as RQJob

    from app.worker.queues import get_redis

    redis = get_redis()
    reaped = 0
    # Correlate by the CURRENT rq id: a resumed summarize job's scheduled resume has a fresh rq
    # id (!= the db id), so fetching by db id would miss it and reap a healthy job.
    #
    # Read the ids out FIRST rather than holding ORM rows across the loop. `mark_terminal`
    # commits (and rolls back a lost race) per job, and a job can be deleted while this runs -
    # see `test_mark_terminal_is_a_no_op_for_a_job_that_does_not_exist`, which exists for this
    # caller. Plain ids cannot go stale or raise ObjectDeletedError; mark_terminal re-reads.
    candidates = [
        (job.id, job.rq_job_id or str(job.id))
        for job in session.scalars(select(Job).where(Job.state.in_(ACTIVE_STATES))).all()
    ]
    for job_id, rq_id in candidates:
        try:
            status = RQJob.fetch(rq_id, connection=redis).get_status(refresh=True)
        except NoSuchJobError:
            status = None  # RQ has no record (worker crashed + registry expired) -> orphan
        except RedisError:
            # Everything reaped so far is already committed, one job at a time, so the count
            # returned is what actually persisted. It used to commit once after the loop, so a
            # blip here returned a non-zero count for jobs the session then discarded unwritten
            # and startup logged "interrupted N stale job(s)" having interrupted none.
            logger.warning("orphan recovery skipped: Redis unreachable")
            return reaped
        if status in _HEALTHY_RQ_STATUSES:
            continue
        # THE single writer for a terminal outcome that is not the job's own success, and its
        # docstring names this function as one of the racers it exists to serialise ("abandoned
        # job cleanup can overlap boot-time orphan recovery"). This was the one party still
        # hand-writing the transition, so it had no conditional UPDATE: a Force stop landing
        # during the loop was overwritten as `interrupted`, leaving the reviewer's deliberate
        # cancel reported as a worker crash - and `stage` still reading "cancelled", because
        # the hand-written version did not set that either.
        if mark_terminal(
            session,
            job_id,
            "interrupted",
            document_status="interrupted",
            document_status_only_when=INTERRUPTIBLE_DOCUMENT_STATUSES,
        ):
            reaped += 1
    return reaped
