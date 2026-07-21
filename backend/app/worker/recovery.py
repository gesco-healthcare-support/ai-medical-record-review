"""Heartbeat-aware orphan recovery for the RQ pipeline.

Replaces the Flask job_queue's mark-ALL-queued/running-interrupted-at-boot sweep, which would kill
healthy in-flight jobs on a rolling restart with N workers. Instead we reconcile each DB job stuck
in queued/running against its RQ counterpart (the RQ job id == the DB job id): a job whose RQ job is
gone or in a terminal state (its worker died) is marked interrupted; a job RQ still reports as
queued/started/deferred/scheduled has a live worker and is left alone.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, Job
from app.services.jobs import ACTIVE_STATES, _utcnow

logger = logging.getLogger(__name__)

# RQ statuses that mean a worker is still on the job (or it is validly waiting to run).
_HEALTHY_RQ_STATUSES = frozenset({"queued", "started", "deferred", "scheduled"})


def recover_orphans(session: Session) -> int:
    """Interrupt DB jobs whose RQ counterpart is gone/terminal; leave healthy ones. Returns the
    count reaped. Safe to call at web startup: it never touches a job a worker is still running."""
    from redis.exceptions import RedisError
    from rq.exceptions import NoSuchJobError
    from rq.job import Job as RQJob

    from app.worker.queues import get_redis

    redis = get_redis()
    reaped = 0
    for job in session.scalars(select(Job).where(Job.state.in_(ACTIVE_STATES))).all():
        try:
            status = RQJob.fetch(str(job.id), connection=redis).get_status(refresh=True)
        except NoSuchJobError:
            status = None  # RQ has no record (worker crashed + registry expired) -> orphan
        except RedisError:
            logger.warning("orphan recovery skipped: Redis unreachable")
            return reaped
        if status in _HEALTHY_RQ_STATUSES:
            continue
        job.state = "interrupted"
        job.finished_at = _utcnow()
        document = session.get(Document, job.document_id)
        if document is not None and document.status in ("segmenting", "summarizing"):
            document.status = "interrupted"
        reaped += 1
    session.commit()
    return reaped
