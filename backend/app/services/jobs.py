"""DB-backed job service for the RQ pipeline (replaces the Flask in-process job_queue).

`create_job` inserts a queued Job + advances Document.status, relying on the DB partial-unique
index (one active job per document) for the cross-process invariant - a racing second insert hits
IntegrityError -> JobConflict (the 409). `enqueue` then dispatches to the kind's RQ queue by dotted
path (the Redis payload is just the job id - non-PHI). The RQ worker is the single writer of
Document.status after enqueue; provenance (model/prompt_version/catalog_revision) is stamped here
and carried as the Job row.
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Document, Job

# `classify` (P6 individual-record auto-categorization) shares segment's status transitions:
# it prepares rows for review, so done -> reviewing. `dedup` (duplicate clustering) runs in the
# background AFTER identify while the reviewer works, so it keeps document.status "reviewing" on both
# enqueue and done - it never changes the pipeline stage the UI shows.
STATUS_ON_ENQUEUE = {
    "segment": "segmenting",
    "classify": "segmenting",
    "summarize": "summarizing",
    "dedup": "reviewing",
}
STATUS_ON_DONE = {
    "segment": "reviewing",
    "classify": "reviewing",
    "summarize": "done",
    "dedup": "reviewing",
}
# Where a CANCELLED job leaves the document. Not the same as STATUS_ON_DONE, and segment is the reason
# why: a cancelled first segment run has no rows, and "reviewing" would open an empty editor as though
# segmentation had succeeded and found nothing in the record. "uploaded" is honest - the work has not
# been done - and it is also what the Start / Re-run controls key off, so the reviewer gets an obvious
# way forward. The other three ran against rows that already exist, so "reviewing" renders whatever
# partial output was committed, which the reviewer is entitled to see.
STATUS_ON_CANCEL = {
    "segment": "uploaded",
    "classify": "reviewing",
    "summarize": "reviewing",
    "dedup": "reviewing",
}
# `paused` is a resumable summarize run awaiting its delayed resume (item 7): still in-flight, so
# it blocks a second job for the same document and is inspected by orphan recovery.
ACTIVE_STATES = ("queued", "running", "paused")


class JobConflict(Exception):
    """A job is already active for the document (the one-active-job invariant)."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def mark_terminal(
    session: Session,
    job_id,
    state: str,
    *,
    stage: str | None = None,
    done: int | None = None,
    total: int | None = None,
    document_status: str | None = None,
    document_status_only_when: tuple[str, ...] | None = None,
) -> bool:
    """Move an ACTIVE job to a terminal state exactly once. True if this call made the transition.

    The single writer for every terminal outcome that is not the job's own success, so a stop
    finalizes identically whether the work-horse cooperated or was killed - the two paths cannot
    drift apart.

    Idempotent on purpose, because several processes legitimately race to finalize the same job:
    RQ runs the stopped callback AND THEN handle_job_failure for one stop; abandoned-job cleanup
    can overlap boot-time orphan recovery; and as the worker fleet grows, parent workers finalize
    concurrently. The conditional UPDATE on ACTIVE_STATES is what makes that safe - it is resolved
    by the database, so the first writer wins and later ones become no-ops instead of overwriting
    an outcome the reviewer has already been shown.

    ``document_status_only_when`` narrows the document write to those statuses, mirroring orphan
    recovery: a background dedup leaves the document "reviewing", and marking that "interrupted"
    would report a failed review over a job the reviewer never watched.
    """
    job = session.get(Job, int(job_id))
    if job is None:
        return False

    values: dict = {"state": state, "finished_at": _utcnow()}
    if stage is not None:
        values["stage"] = stage
    if done is not None:
        values["current"] = done
    if total is not None:
        values["total"] = total

    changed = session.execute(
        update(Job).where(Job.id == job.id, Job.state.in_(ACTIVE_STATES)).values(**values)
    )
    if not changed.rowcount:
        session.rollback()  # someone else finalized it first; leave their outcome alone
        return False

    if document_status is not None:
        document = session.get(Document, job.document_id)
        if document is not None and (
            document_status_only_when is None or document.status in document_status_only_when
        ):
            document.status = document_status
    session.commit()
    session.expire(job)  # the identity-mapped copy still holds the pre-UPDATE state
    return True


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
    title_model: str | None = None,
    audit_model: str | None = None,
) -> Job:
    """Insert a queued Job + advance Document.status; raise JobConflict if one is already active.

    The DB partial-unique index is the real guard - it survives a cross-process race the old
    in-process lock could not. Commits on success.

    Provenance is resolved HERE, not at the call sites, because this is the single seam every job
    passes through - six callers each remembering to stamp it is six chances to forget:

    * ``title_model`` / ``audit_model`` default from ``Settings.model_for`` for a summarize job and
      stay NULL for every other kind, which makes no title or audit call. Resolved once so a job
      resumed after a config change keeps the models it started with. ``model`` is left exactly as
      the caller passed it - a per-request model choice must not be silently overridden.
    * ``prompt_fingerprint`` hashes the prompt set in play, resolved DB-first, and is fail-safe: a
      stamp that cannot be computed must never stop a job from starting.
    """
    from app.config import get_settings
    from app.services.provenance import job_prompt_fingerprint

    if kind == "summarize":
        settings = get_settings()
        title_model = title_model or settings.model_for("title")
        audit_model = audit_model or settings.model_for("audit")
    job = Job(
        document_id=document_id,
        kind=kind,
        model=model,
        title_model=title_model,
        audit_model=audit_model,
        prompt_version=prompt_version,
        prompt_fingerprint=job_prompt_fingerprint(session, kind),
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
    title_model: str | None = None,
    audit_model: str | None = None,
) -> Job:
    """create_job + dispatch to the kind's RQ queue. If the dispatch fails (e.g. Redis down), the
    job is marked interrupted rather than left stuck queued.

    ``title_model`` / ``audit_model`` are passed straight through; create_job resolves them.
    """
    from rq import Callback

    from app.config import get_settings
    from app.worker.finalizers import on_job_failed, on_job_stopped
    from app.worker.queues import queue_for, worker_fn

    job = create_job(
        session,
        document_id,
        kind,
        model=model,
        prompt_version=prompt_version,
        catalog_revision=catalog_revision,
        title_model=title_model,
        audit_model=audit_model,
    )
    try:
        # RQ job id == the DB job id, so heartbeat orphan recovery can correlate the two.
        # Size-aware job_timeout: RQ's 180s default is far too short for large records, and a flat
        # cap either starves big docs or makes small ones hang. Scale by page count.
        settings = get_settings()
        document = session.get(Document, document_id)
        pages = getattr(document, "page_count", 0) or 0
        timeout = settings.effective_job_timeout(pages)
        # Route onto the OWNER's lane so one tester's backlog cannot serialise the others (measured
        # 2026-07-30: a 427-second unstarted wait behind another user's job). A document with no owner
        # falls back to the base queue, which every worker still listens to.
        rq_job = queue_for(kind, getattr(document, "user_id", None)).enqueue(
            worker_fn(kind),
            job.id,
            job_id=str(job.id),
            job_timeout=timeout,
            # A killed work-horse cannot finalize itself, so the PARENT worker does it. Without
            # these the row stays "running" forever and wedges the document until the API restarts.
            # Wrapped in Callback because rq 2.10 deprecates passing a bare function.
            on_stopped=Callback(on_job_stopped),
            on_failure=Callback(on_job_failed),
        )
        # Record the RQ job id so orphan recovery can correlate it. On the first run this equals
        # str(job.id); a resumable summarize pause reassigns it to the fresh scheduled resume.
        job.rq_job_id = rq_job.id
        session.commit()
    except Exception:
        job.state = "interrupted"
        job.finished_at = _utcnow()
        document = session.get(Document, document_id)
        if document is not None:
            document.status = "interrupted"
        session.commit()
        raise
    return job
