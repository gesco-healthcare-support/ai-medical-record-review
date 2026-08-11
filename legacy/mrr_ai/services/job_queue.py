"""DB-backed job queue for concurrent document pipelines.

Replaces the single-slot in-memory runner (services/jobs.py, kept for the classic UI)
in the document-scoped flow: jobs live in the DB so polling, provenance, and restarts
all read one source of truth, and they run on a small in-process thread pool
(PIPELINE_WORKERS, default 2 - every job shares one Vertex quota pool, so more workers
mostly buys 429 storms).

This module is the ONLY writer of Document.status after upload (single-writer status
machine per the plan); targets own their result persistence and run inside an app
context with their thread's own scoped session.

SINGLE-PROCESS constraint: the pool lives in this process. A multi-process server
would run submissions per-process and double-execute nothing but confuse everything;
serve.py runs waitress with one process.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from flask import current_app

from mrr_ai.config import PIPELINE_WORKERS
from mrr_ai.errors import user_facing_message

# Document.status transitions driven by job lifecycle.
_STATUS_ON_SUBMIT = {"segment": "segmenting", "summarize": "summarizing"}
_STATUS_ON_DONE = {"segment": "reviewing", "summarize": "done"}

# Minimum seconds between progress UPDATEs: categorization reports per row and SQLite
# has one writer - unthrottled progress would contend with the job's own inserts.
PROGRESS_MIN_INTERVAL = 1.0

# Serializes the check-then-insert in submit(): two request threads submitting for the
# same document must not both pass the active-job check.
_submit_lock = threading.Lock()


def _utcnow():
    return datetime.now(UTC)


def init_app(app):
    """Create the pool and reconcile jobs orphaned by the previous process."""
    app.extensions["job_queue"] = ThreadPoolExecutor(
        max_workers=PIPELINE_WORKERS, thread_name_prefix="pipeline"
    )
    with app.app_context():
        sweep_orphans()


def sweep_orphans():
    """Mark queued/running jobs from a dead process as interrupted (with their docs).

    Called at boot: a crash or restart cannot leave phantom 'running' rows, or the UI
    would poll them forever and submit() would refuse new work on those documents.
    """
    from mrr_ai.extensions import db
    from mrr_ai.models import Document, Job

    orphans = Job.query.filter(Job.state.in_(("queued", "running"))).all()
    for job in orphans:
        job.state = "interrupted"
        job.finished_at = _utcnow()
        document = db.session.get(Document, job.document_id)
        if document is not None:
            document.status = "interrupted"
    db.session.commit()
    return len(orphans)


def active_job(document_id):
    """The document's queued/running job, or None."""
    from mrr_ai.models import Job

    return Job.query.filter(
        Job.document_id == document_id, Job.state.in_(("queued", "running"))
    ).first()


def submit(document_id, kind, target, *, model, prompt_version, catalog_revision=None):
    """Queue ``target(report)`` for a document; None if it already has an active job.

    ``target`` runs on the pool inside an app context; it receives
    ``report(stage, current, total)`` and owns persisting its own results (rows,
    summaries) - this module only manages Job/Document state. ``catalog_revision`` records
    the category/prompt catalog version the run used (provenance).
    """
    from mrr_ai.extensions import db
    from mrr_ai.models import Document, Job

    app = current_app._get_current_object()
    with _submit_lock:
        if active_job(document_id) is not None:
            return None
        job = Job(
            document_id=document_id,
            kind=kind,
            model=model,
            prompt_version=prompt_version,
            catalog_revision=catalog_revision,
        )
        document = db.session.get(Document, document_id)
        document.status = _STATUS_ON_SUBMIT[kind]
        db.session.add(job)
        db.session.commit()
        job_id = job.id

    app.extensions["job_queue"].submit(_run, app, job_id, target)
    return job


def _run(app, job_id, target):
    """Pool worker: run the target under an app context and drive the state machine."""
    with app.app_context():
        from mrr_ai.extensions import db
        from mrr_ai.models import Document, Job

        job = db.session.get(Job, job_id)
        job.state = "running"
        job.started_at = _utcnow()
        db.session.commit()

        last_write = 0.0

        def report(stage, current, total):
            nonlocal last_write
            now = time.monotonic()
            # Stage changes always write (the UI keys its label off them); same-stage
            # ticks are rate-limited.
            if stage == job.stage and now - last_write < PROGRESS_MIN_INTERVAL:
                return
            job.stage, job.current, job.total = stage, current, total
            db.session.commit()
            last_write = now

        try:
            target(report)
        except Exception as exc:  # noqa: BLE001 - any target failure becomes a user-facing error
            db.session.rollback()  # the target may have died mid-transaction
            # Log the technical detail server-side; show the user a friendly message, never a
            # raw stack trace or vendor API error (see mrr_ai/errors.py).
            current_app.logger.exception("job %s failed", job_id)
            job = db.session.get(Job, job_id)
            job.state = "error"
            job.error = user_facing_message(exc)
            job.finished_at = _utcnow()
            document = db.session.get(Document, job.document_id)
            document.status = "error"
            db.session.commit()
            return

        job.state = "done"
        job.finished_at = _utcnow()
        document = db.session.get(Document, job.document_id)
        document.status = _STATUS_ON_DONE[job.kind]
        db.session.commit()
