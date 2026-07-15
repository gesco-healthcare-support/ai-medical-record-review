"""DB-backed job service for the RQ pipeline (replaces the Flask in-process job_queue).

`create_job` inserts a queued Job + advances Document.status, relying on the DB partial-unique
index (one active job per document) for the cross-process invariant - a racing second insert hits
IntegrityError -> JobConflict (the 409). `enqueue` then dispatches to the kind's RQ queue by dotted
path (the Redis payload is just the job id - non-PHI). The RQ worker is the single writer of
Document.status after enqueue; provenance (model/prompt_version/catalog_revision) is stamped here
and carried as the Job row.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Document, Job

STATUS_ON_ENQUEUE = {"segment": "segmenting", "summarize": "summarizing"}
STATUS_ON_DONE = {"segment": "reviewing", "summarize": "done"}
ACTIVE_STATES = ("queued", "running")


class JobConflict(Exception):
    """A job is already active for the document (the one-active-job invariant)."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def active_job(session: Session, document_id: str) -> Job | None:
    """The document's queued/running job, or None."""
    return session.scalar(
        select(Job).where(Job.document_id == document_id, Job.state.in_(ACTIVE_STATES))
    )


def create_job(
    session: Session,
    document_id: str,
    kind: str,
    *,
    model: str,
    prompt_version: str,
    catalog_revision: int | None = None,
) -> Job:
    """Insert a queued Job + advance Document.status; raise JobConflict if one is already active.

    The DB partial-unique index is the real guard - it survives a cross-process race the old
    in-process lock could not. Commits on success.
    """
    job = Job(
        document_id=document_id,
        kind=kind,
        model=model,
        prompt_version=prompt_version,
        catalog_revision=catalog_revision,
    )
    session.add(job)
    document = session.get(Document, document_id)
    document.status = STATUS_ON_ENQUEUE[kind]
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise JobConflict(f"a job is already active for document {document_id}") from exc
    return job


def enqueue(
    session: Session,
    document_id: str,
    kind: str,
    *,
    model: str,
    prompt_version: str,
    catalog_revision: int | None = None,
) -> Job:
    """create_job + dispatch to the kind's RQ queue. If the dispatch fails (e.g. Redis down), the
    job is marked interrupted rather than left stuck queued."""
    from app.worker.queues import queue_for, worker_fn

    job = create_job(
        session,
        document_id,
        kind,
        model=model,
        prompt_version=prompt_version,
        catalog_revision=catalog_revision,
    )
    try:
        # RQ job id == the DB job id, so heartbeat orphan recovery can correlate the two.
        queue_for(kind).enqueue(worker_fn(kind), job.id, job_id=str(job.id))
    except Exception:
        job.state = "interrupted"
        job.finished_at = _utcnow()
        document = session.get(Document, document_id)
        if document is not None:
            document.status = "interrupted"
        session.commit()
        raise
    return job
