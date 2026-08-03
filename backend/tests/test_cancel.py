"""Cancel channel: the Redis flag, the per-process current job, and the worker's cooperative stop.

Why a Redis key at all, when `Job.cancel_requested` is the durable record: the signal has to reach
`generate_with_retry`, which runs deep inside a ThreadPoolExecutor worker with no session and no job
argument. A cheap GET there is what turns "wedged in backoff for 17 minutes" into "stops in a second".

The per-process global is correct because RQ's Worker FORKS a work-horse per job (verified against the
installed rq 2.10.0), so one process only ever runs one job - and unlike a contextvar, a module global
IS visible from the pool threads, because ThreadPoolExecutor does not copy context into its workers.
"""

import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Document, Job, ReviewRow, Summary, User
from app.worker import cancel as cancel_mod
from app.worker.failures import JobCancelled


@pytest.fixture(autouse=True)
def _clean_current_job():
    cancel_mod.clear_current_job()
    yield
    cancel_mod.clear_current_job()


def test_requested_cancel_is_visible_then_clearable():
    """WHEN a cancel is requested for a job, THE SYSTEM SHALL report it, and SHALL stop reporting it
    once cleared."""
    cancel_mod.request_cancel(90001)
    assert cancel_mod.is_cancel_requested(90001) is True
    cancel_mod.clear_cancel(90001)
    assert cancel_mod.is_cancel_requested(90001) is False


def test_an_unrequested_job_is_not_cancelled():
    assert cancel_mod.is_cancel_requested(90002) is False


def test_current_job_cancelled_needs_no_redis_when_no_job_is_set(monkeypatch):
    """WHEN no current job is set, THE SYSTEM SHALL return false WITHOUT touching Redis.

    generate_with_retry consults this on every backoff slice, including in the API process where no
    job is running, so the no-job path must not cost a round trip.
    """

    def explode():
        raise AssertionError("Redis must not be consulted when no job is current")

    monkeypatch.setattr(cancel_mod, "get_redis", explode)
    assert cancel_mod.current_job_cancelled() is False


def test_current_job_tracks_the_running_job():
    cancel_mod.set_current_job(90003)
    assert cancel_mod.current_job_cancelled() is False
    cancel_mod.request_cancel(90003)
    try:
        assert cancel_mod.current_job_cancelled() is True
    finally:
        cancel_mod.clear_cancel(90003)


def test_a_redis_failure_reports_not_cancelled(monkeypatch):
    """IF Redis raises, THEN THE SYSTEM SHALL report NOT cancelled rather than propagating.

    Failing closed is deliberate. The alternative - treating an unreachable Redis as "cancelled" -
    would let one blip abort every job running anywhere, which is far worse than a stop button that
    briefly does nothing.
    """
    from redis.exceptions import RedisError

    class Boom:
        def get(self, *_a, **_k):
            raise RedisError("down")

        def set(self, *_a, **_k):
            raise RedisError("down")

        def delete(self, *_a, **_k):
            raise RedisError("down")

    monkeypatch.setattr(cancel_mod, "get_redis", lambda: Boom())
    assert cancel_mod.is_cancel_requested(90004) is False
    cancel_mod.request_cancel(90004)  # must not raise either
    cancel_mod.clear_cancel(90004)  # nor must the cleanup path

    # Only RedisError is tolerated. A stub missing a method entirely raises AttributeError, and that
    # must still propagate - swallowing every exception here would hide a real bug in this module
    # behind a permanently "not cancelled" answer.
    class Wrong:
        pass

    monkeypatch.setattr(cancel_mod, "get_redis", lambda: Wrong())
    with pytest.raises(AttributeError):
        cancel_mod.is_cancel_requested(90005)


def _seed_document(pages: int = 4) -> str:
    """A user + document to hang jobs off. No PDF is needed: these tests never reach OCR."""
    import uuid

    from app.auth.password import MrrPasswordHelper
    from tests.conftest import unique_test_email

    doc_id = str(uuid.uuid4())
    with get_sessionmaker()() as session:
        user = User(
            email=unique_test_email(),
            name="Cancel",
            password=MrrPasswordHelper().hash("Str0ng#pw1"),
            active=True,
        )
        session.add(user)
        session.flush()
        session.add(
            Document(
                id=doc_id,
                user_id=user.id,
                original_filename="synthetic.pdf",
                stored_path="/nonexistent/synthetic.pdf",
                sha256="f" * 64,
                page_count=pages,
                status="uploaded",
            )
        )
        session.commit()
    return doc_id


def _job(doc_id: str, kind: str = "segment", state: str = "queued") -> int:
    with get_sessionmaker()() as session:
        job = Job(document_id=doc_id, kind=kind, state=state, model="m", prompt_version="1")
        session.add(job)
        session.commit()
        return job.id


def test_report_raises_cancelled_even_when_the_tick_would_be_throttled():
    """WHEN a cancel is requested, THE SYSTEM SHALL raise at the next report() even if that tick falls
    inside _PROGRESS_MIN_INTERVAL.

    The throttle exists to stop per-row progress writes contending with the job's own inserts. If the
    cancel check sat AFTER the early return, a job reporting faster than once a second - which is the
    normal case for summarize and classify - could swallow the stop indefinitely.
    """
    from app.worker.tasks import _run

    doc_id = _seed_document()
    job_id = _job(doc_id)
    cancel_mod.request_cancel(job_id)
    seen = []

    def work(session, job, report):
        report("segmenting", 0, 10)  # first tick: always writes, and must raise here
        seen.append("kept going")

    try:
        _run(job_id, work)
    finally:
        cancel_mod.clear_cancel(job_id)

    assert seen == []  # work() did not continue past the cancelled report
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "cancelled"
        assert job.finished_at is not None


def test_cancel_leaves_a_coherent_document_status():
    """WHEN a segment job is cancelled, THE SYSTEM SHALL set a document status that does not claim the
    run finished. `reviewing` would render an empty editor as though segmentation found nothing."""
    from app.worker.tasks import _run

    doc_id = _seed_document()
    job_id = _job(doc_id)
    cancel_mod.request_cancel(job_id)

    def work(session, job, report):
        report("segmenting", 0, 3)

    try:
        _run(job_id, work)
    finally:
        cancel_mod.clear_cancel(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Document, doc_id).status == "uploaded"


def test_cancelling_a_segment_job_never_wipes_the_rows():
    """WHILE a segment job is cancelled during compute, THE SYSTEM SHALL leave existing ReviewRows
    untouched.

    This is the single most dangerous failure in this feature. segment_document runs
    DELETE FROM review_rows and re-inserts inside ONE work() call, so a cancel check reachable between
    the two would commit a document with zero rows and destroy the reviewer's page ranges. The check
    fires only via report(), which is passed into run_segmentation - the write phase calls no report().
    """
    from app.worker.tasks import _run

    doc_id = _seed_document()
    with get_sessionmaker()() as session:
        for idx in range(3):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=idx + 1,
                    end=idx + 1,
                    category="1",
                    title=f"Doc {idx}",
                    date="-",
                    injury_date="-",
                    flag="-",
                    include=True,
                )
            )
        session.commit()

    job_id = _job(doc_id)
    cancel_mod.request_cancel(job_id)

    def work(session, job, report):
        # Stands in for run_segmentation: reports during compute, before any row write.
        report("segmenting", 0, 2)
        raise AssertionError("must not reach the write phase")

    try:
        _run(job_id, work)
    finally:
        cancel_mod.clear_cancel(job_id)

    with get_sessionmaker()() as session:
        rows = session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
    assert len(rows) == 3  # every page range survived


def test_cancel_keeps_work_already_committed():
    """WHEN a job is cancelled, THE SYSTEM SHALL keep output it had already committed.

    _finalize_cancelled must NOT rollback (mirroring _finalize_paused): a cancelled summarize's
    finished summaries are the reviewer's, and hiding completed work would be the surprising choice.
    """
    from app.worker.tasks import _run

    doc_id = _seed_document()
    job_id = _job(doc_id, kind="summarize")

    def work(session, job, report):
        report("summarizing", 0, 2)
        session.add(
            Summary(
                document_id=doc_id,
                job_id=job.id,
                idx=0,
                title="Kept",
                text="body",
                row_start=1,
                row_end=1,
                row_category="1",
            )
        )
        session.commit()  # committed before the cancel is noticed
        cancel_mod.request_cancel(job_id)
        report("summarizing", 1, 2)

    try:
        _run(job_id, work)
    finally:
        cancel_mod.clear_cancel(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "cancelled"
        kept = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
    assert len(kept) == 1 and kept[0].title == "Kept"


def test_cancel_clears_the_redis_flag_so_a_restart_is_not_born_cancelled():
    """WHEN a cancelled job finalizes, THE SYSTEM SHALL clear its Redis key.

    The key has a TTL, but a restart enqueued inside that window would otherwise inherit a stale flag
    if the id were ever reused, and a leaked key is a landmine that is hard to attribute later.
    """
    from app.worker.tasks import _run

    doc_id = _seed_document()
    job_id = _job(doc_id)
    cancel_mod.request_cancel(job_id)

    def work(session, job, report):
        report("segmenting", 0, 1)

    _run(job_id, work)

    assert cancel_mod.is_cancel_requested(job_id) is False


def test_a_cancelled_job_is_not_active_so_a_restart_can_start():
    """WHERE a job's state is cancelled, THE SYSTEM SHALL NOT report it from active_job()."""
    from app.services.jobs import active_job

    doc_id = _seed_document()
    job_id = _job(doc_id, state="cancelled")
    with get_sessionmaker()() as session:
        assert active_job(session, doc_id) is None
        assert session.get(Job, job_id).state == "cancelled"


def test_cancel_requested_defaults_to_false():
    doc_id = _seed_document()
    job_id = _job(doc_id)
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).cancel_requested is False


def test_job_cancelled_carries_progress_for_the_finalizer():
    sig = JobCancelled(done=4, total=9)
    assert (sig.done, sig.total) == (4, 9)


def test_a_job_deleted_before_the_horse_starts_is_not_an_error():
    """WHEN the job row is gone by the time the work-horse runs, THE SYSTEM SHALL log and return.

    The queue outlives the database row: a document deleted while its job sat queued leaves an RQ job
    naming a row that no longer exists. Raising there would fail the RQ job and, now that failures are
    finalized by a callback, write a terminal state for a job nobody can look up.
    """
    from app.worker.tasks import _run

    ran = []
    _run(2_000_000_003, lambda session, job, report: ran.append("ran"))
    assert ran == []


def test_current_job_id_reports_the_running_job():
    """The accessor the retry backoff reads. It has no session and no job argument, so this global is
    the only way it can know which job it belongs to."""
    cancel_mod.set_current_job(90123)
    assert cancel_mod.current_job_id() == 90123
    cancel_mod.clear_current_job()
    assert cancel_mod.current_job_id() is None


def test_a_pause_that_cannot_be_scheduled_is_marked_interrupted_not_left_paused(monkeypatch):
    """WHEN scheduling a paused run's resume fails, THE SYSTEM SHALL mark it interrupted, not paused.

    `paused` counts as active for the one-active-job index, so a pause that never gets its resume
    scheduled - Redis down at exactly that moment - would wedge the document behind a job no worker is
    ever going to pick up. Failing visibly is the only recoverable outcome.
    """
    from app.worker import tasks as tasks_mod
    from app.worker.failures import JobPaused

    def boom(*_a, **_kw):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(tasks_mod, "queue_for", boom)

    doc_id = _seed_document()
    job_id = _job(doc_id)

    def work(session, job, report):
        raise JobPaused(delay=30, done=2, total=9)

    tasks_mod._run(job_id, work)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "interrupted"
        assert job.finished_at is not None
        assert session.get(Document, doc_id).status == "interrupted"
