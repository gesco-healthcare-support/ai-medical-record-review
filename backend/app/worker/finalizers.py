"""Terminal-state writers for jobs whose work-horse cannot write its own outcome.

RQ forks a work-horse per job. When that fork dies without finishing - the reviewer's Force stop,
an OOM, a segfault - the code that would have finalized the Job row dies with it, so the row stays
`running` forever. That is not cosmetic: the UI polls the job, sees an active state and spins its
progress bar indefinitely, and the one-active-job partial index blocks every new run on that
document. Before these callbacks the only reconciler was `recover_orphans` at API startup, so a
force-stopped document stayed wedged until somebody restarted the API.

These run in the PARENT worker, which outlives the fork it killed, so they can still reach the
database. Verified against the installed rq 2.10.0:

  - ``on_stopped`` <- ``Worker.monitor_work_horse`` -> ``execute_stopped_callback``
    (rq/worker/worker_classes.py:135), for a deliberate stop-job command.
  - ``on_failure``  <- ``execute_failure_callback``, from two places: in-horse for an ordinary
    exception (rq/worker/base.py:1585), and from ``StartedJobRegistry.cleanup`` with
    ``AbandonedJobError`` (rq/registry.py:283) for a horse that died without reporting.

The abandoned path is eventual, not immediate: it only fires once the job's entry outlives
``job_timeout``, which here is size-aware and can be long. The deliberate stop is immediate, which
is the case a reviewer is watching.

A HORSE THE SYSTEM KILLS reaches neither. When the kernel kills the fork - out of memory, as a
record with pages declared 154 x 200 inches did to a reviewer's Identify on the live box
(2026-11-13) - ``Worker.monitor_work_horse`` logs "Work-horse terminated unexpectedly", calls
``handle_work_horse_killed`` and moves the RQ job straight to the FailedJobRegistry. That is not the
StartedJobRegistry, so abandoned-job cleanup never sees it and ``on_failure`` never runs: the row
stayed `running` for days, and Stop and Delete both refused it, until the API restarted and orphan
recovery ran. ``on_work_horse_killed`` is RQ's hook for that moment (``work_horse_killed_handler``,
rq/worker/base.py), registered on the worker in `app.worker.__main__`. It too runs in the parent.

Registered at enqueue in `app.services.jobs.enqueue` and on the resume dispatch in
`app.worker.tasks`. RQ serializes callbacks by reference, so these must stay importable by dotted
path - do not make them closures or move them without updating both call sites.
"""

import logging

from app.db import get_sessionmaker
from app.services.jobs import INTERRUPTIBLE_DOCUMENT_STATUSES, STATUS_ON_CANCEL, mark_terminal

logger = logging.getLogger(__name__)


def _db_job_id(rq_job) -> int | None:
    """The DB job id this RQ job runs, read from its first argument.

    NOT ``rq_job.id``: a resumed summarize is dispatched under a fresh RQ id (tasks.py, enqueue_in),
    so correlating by RQ id would silently fail to finalize exactly the long-running jobs most
    likely to be stopped. Both dispatch sites pass the DB job id as the first positional argument.
    """
    try:
        return int(rq_job.args[0])
    except (AttributeError, IndexError, TypeError, ValueError):
        logger.warning("cannot correlate RQ job %s to a DB job", getattr(rq_job, "id", "?"))
        return None


def on_job_stopped(rq_job, _connection) -> None:
    """A deliberate stop: the reviewer pressed Force stop and the work-horse was killed.

    ``_connection`` is unused but not removable: RQ calls a stopped-callback positionally with the
    job and its Redis connection, so the arity is the framework's, not ours.

    Terminal state matches the cooperative stop exactly - same writer, same STATUS_ON_CANCEL - so
    which of the two paths ended the run is invisible downstream, as it should be.
    """
    job_id = _db_job_id(rq_job)
    if job_id is None:
        return
    try:
        with get_sessionmaker()() as session:
            from app.models import Job

            job = session.get(Job, job_id)
            if job is None:
                return
            if mark_terminal(
                session,
                job_id,
                "cancelled",
                stage="cancelled",
                document_status=STATUS_ON_CANCEL.get(job.kind),
            ):
                logger.info("job %s finalized as cancelled by the stopped callback", job_id)
    except Exception:
        # Never raise out of a callback: RQ logs and re-raises, which would take down the parent
        # worker's job-monitoring loop over a database blip. Boot orphan recovery is the backstop.
        logger.exception("stopped callback could not finalize job %s", job_id)


def _interrupt(job_id: int, by: str) -> None:
    """Finalize an active job as "interrupted" - and its document only when it is mid-pipeline,
    as `recover_orphans` does. Never raises (see `on_job_stopped`)."""
    try:
        with get_sessionmaker()() as session:
            if mark_terminal(
                session,
                job_id,
                "interrupted",
                document_status="interrupted",
                document_status_only_when=INTERRUPTIBLE_DOCUMENT_STATUSES,
            ):
                logger.info("job %s finalized as interrupted by the %s", job_id, by)
    except Exception:
        logger.exception("%s could not finalize job %s", by, job_id)


def on_job_failed(rq_job, _connection, *_exc_info) -> None:
    """The work-horse failed or was abandoned.

    ``_connection`` and ``_exc_info`` are unused but not removable, for the same reason as the
    stopped callback: RQ supplies both positionally. The exception is not read here because `_run`
    has already recorded it; this callback exists for the case where the horse never got that far.

    Mirrors `recover_orphans`: "interrupted", and the document only when it is mid-pipeline. An
    ordinary in-horse exception has already been finalized as "error" by `_run`, so this is a
    no-op there - which is precisely what mark_terminal's idempotency is for.
    """
    job_id = _db_job_id(rq_job)
    if job_id is not None:
        _interrupt(job_id, "failure callback")


# Reading an uploaded record's pages (the `ocr` queue) has no jobs row on purpose; a horse killed
# there leaves nothing to finalize.
_UNTRACKED_TASKS = ("ocr_uploaded_document",)


def on_work_horse_killed(rq_job, _retpid, ret_val, _rusage) -> None:
    """The system killed the work-horse - out of memory, or any signal nobody here sent. See the
    note atop this module: RQ calls this in the parent, and nothing else ever finalizes the row.

    Finalized exactly as an abandoned job is, "interrupted", so the reviewer sees a run that can be
    started again instead of one that never ends. A Force stop is not this path: RQ sends it to the
    stopped callback.
    """
    if (getattr(rq_job, "func_name", None) or "").endswith(_UNTRACKED_TASKS):
        logger.warning("reading of an uploaded record was killed (waitpid %s)", ret_val)
        return
    job_id = _db_job_id(rq_job)
    if job_id is not None:
        logger.warning("work-horse for job %s was killed (waitpid %s)", job_id, ret_val)
        _interrupt(job_id, "killed-work-horse handler")
