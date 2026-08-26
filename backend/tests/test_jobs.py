"""P4a: the job service (one-active-job invariant + enqueue routing) and the worker state machine.

The invariant + state-machine tests run against docker Postgres (the partial-unique index is a
real DB constraint); the pipeline itself (run_segmentation / summarize_row) is MOCKED so no torch
or Vertex call is needed. Test users use the pytest-auth- prefix so conftest cleans them + their
jobs/rows.
"""

import time
import uuid

import pytest
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.errors import EmptyExtractionError, OcrUnavailableError
from app.models import Document, Job, ReviewRow, SegmentRow, Summary, User
from app.services import jobs
from app.worker.queues import queue_for, worker_fn
from app.worker.tasks import _run, dedup_document, segment_document, summarize_document
from tests.conftest import lanes, unique_test_email


def _make_user_and_doc(page_count: int = 2) -> str:
    """Insert a test user + a document (no file on disk needed for these tests); return doc id."""
    with get_sessionmaker()() as session:
        user = User(
            email=unique_test_email(),
            name="Jobs",
            password=MrrPasswordHelper().hash("Str0ng#pw1"),
            active=True,
        )
        session.add(user)
        session.flush()
        document = Document(
            id=str(uuid.uuid4()),
            user_id=user.id,
            original_filename="synthetic.pdf",
            stored_path="/nonexistent/synthetic.pdf",
            sha256="0" * 64,
            page_count=page_count,
        )
        session.add(document)
        session.commit()
        return document.id


def test_queue_routing_maps_kind_to_queue_and_task():
    assert queue_for("segment", 7).name == "segment:7"
    assert queue_for("summarize", 7).name == "summarize:7"
    assert worker_fn("segment").endswith("tasks.segment_document")
    assert worker_fn("summarize").endswith("tasks.summarize_document")


def test_classify_and_dedup_keep_riding_their_task_queue():
    # The user lane is orthogonal to the task split: classify still needs the torch image and dedup
    # still does not, so adding lanes must not move either onto the wrong worker.
    assert queue_for("classify", 7).name == "segment:7"
    assert queue_for("dedup", 7).name == "summarize:7"


def test_a_job_with_no_owner_falls_back_to_the_base_queue():
    # A document with no user has nowhere else to go, and jobs enqueued before lanes existed sit on
    # the bare name. Every worker listens to it, so neither is stranded.
    assert queue_for("segment").name == "segment"
    assert queue_for("segment", None).name == "segment"


def test_worker_listens_to_the_base_plus_one_lane_per_user():
    # WHEN a worker starts, THE SYSTEM SHALL listen to the base queue AND one lane per user, so a
    # pre-lane job is still claimed and each user has a lane of their own.
    from app.worker.queues import lanes_for

    assert lanes_for("segment", [2, 3, 4]) == ["segment", "segment:2", "segment:3", "segment:4"]
    assert lanes_for("segment", []) == ["segment"]
    # Duplicate ids collapse; the base is never repeated as a lane.
    assert lanes_for("summarize", [3, 3]) == ["summarize", "summarize:3"]


def test_the_worker_dequeues_round_robin_not_by_priority():
    """The whole point of lanes. RQ's default Worker reads its queues in STRICT PRIORITY order, so
    listing user lanes on it would starve whichever user sorts last - the head-of-line bug this
    change exists to remove, with extra steps. RoundRobinWorker rotates instead."""
    import inspect

    from rq.worker import RoundRobinWorker, Worker

    from app.worker import __main__ as worker_main

    source = inspect.getsource(worker_main.main)
    assert "RoundRobinWorker(" in source
    assert "Worker(" not in source.replace("RoundRobinWorker(", "")
    # It must still be a Worker, so work(with_scheduler=True) keeps firing delayed summarize resumes
    # (verified live: the scheduler lock is acquired after the class swap).
    assert issubclass(RoundRobinWorker, Worker)
    assert "with_scheduler" in inspect.signature(Worker.work).parameters


class _FakeWorker:
    """Captures how main() builds its worker, so the entrypoint is testable without blocking on work()."""

    last: dict = {}

    def __init__(self, queues, connection=None, **kwargs):
        _FakeWorker.last = {"names": [q.name for q in queues], "kwargs": kwargs}

    def work(self, **kwargs):
        _FakeWorker.last["work_kwargs"] = kwargs


def test_the_entrypoint_expands_lanes_and_passes_the_scheduler_through(monkeypatch):
    # Asserted on what main() actually BUILDS rather than on its source text, which also covers the
    # entrypoint itself - nothing else in the suite calls it.
    from app.worker import __main__ as worker_main

    monkeypatch.setattr(worker_main, "RoundRobinWorker", _FakeWorker)
    monkeypatch.setattr(worker_main, "_user_ids", lambda: [2, 3])
    worker_main.main(["summarize"])

    assert _FakeWorker.last["names"] == ["summarize", "summarize:2", "summarize:3"]
    # with_scheduler must survive the class swap: delayed summarize resumes depend on it.
    assert _FakeWorker.last["work_kwargs"] == {"with_scheduler": True}


def test_the_entrypoint_serves_both_bases_when_given_no_arguments(monkeypatch):
    from app.worker import __main__ as worker_main

    monkeypatch.setattr(worker_main, "RoundRobinWorker", _FakeWorker)
    monkeypatch.setattr(worker_main, "_user_ids", lambda: [4])
    worker_main.main([])
    assert _FakeWorker.last["names"] == ["segment", "segment:4", "summarize", "summarize:4"]


def test_the_entrypoint_rejects_an_unknown_queue(monkeypatch):
    from app.worker import __main__ as worker_main

    monkeypatch.setattr(worker_main, "RoundRobinWorker", _FakeWorker)
    monkeypatch.setattr(worker_main, "_user_ids", lambda: [])
    with pytest.raises(SystemExit):
        worker_main.main(["nonesuch"])


def test_no_users_still_yields_a_workable_base_queue(monkeypatch):
    from app.worker import __main__ as worker_main

    monkeypatch.setattr(worker_main, "RoundRobinWorker", _FakeWorker)
    monkeypatch.setattr(worker_main, "_user_ids", lambda: [])
    worker_main.main(["segment"])
    assert _FakeWorker.last["names"] == ["segment"]


def test_the_user_lookup_fails_soft(monkeypatch):
    """A worker that refuses to boot because it could not list users is worse than one serving fewer
    lanes - a transient DB blip at startup would otherwise stop the whole pipeline."""
    from app.worker import __main__ as worker_main

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.db.get_sessionmaker", boom)
    assert worker_main._user_ids() == []


def test_serving_a_lane_moves_it_to_the_back_of_the_order():
    """The rotation itself, not just the class name: after a queue is served it goes to the BACK, so
    the next dequeue starts from the following lane. A default Worker never reorders, which is exactly
    how the last-listed user starves. Demonstrated end to end in the fairness check on 2026-07-31:
    with one worker and a 5-job backlog, a default Worker ran the second user's job 6th of 6 while
    RoundRobinWorker ran it 2nd."""
    from rq import Queue
    from rq.worker import RoundRobinWorker

    from app.worker.queues import get_redis

    redis = get_redis()
    names = ["segment", "segment:2", "segment:3"]
    worker = RoundRobinWorker([Queue(n, connection=redis) for n in names], connection=redis)
    worker._ordered_queues = list(worker.queues)
    worker.reorder_queues(reference_queue=worker._ordered_queues[0])
    assert [q.name for q in worker._ordered_queues] == ["segment:2", "segment:3", "segment"]
    worker.reorder_queues(reference_queue=worker._ordered_queues[0])
    assert [q.name for q in worker._ordered_queues] == ["segment:3", "segment", "segment:2"]


def test_enqueue_routes_a_job_onto_its_owners_lane():
    # WHEN a job is enqueued, THE SYSTEM SHALL place it on the lane of the document's OWNER, so one
    # tester's backlog cannot serialise another's (measured: a 427-second unstarted wait).
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        owner = session.get(Document, doc_id).user_id
    queue = queue_for("segment", owner)
    queue.empty()
    base = queue_for("segment")
    base.empty()
    try:
        with get_sessionmaker()() as session:
            jobs.enqueue(session, doc_id, "segment", model="m", prompt_version="1")
        assert queue.name == f"segment:{owner}"
        assert queue.count == 1
        # And NOT on the shared base queue, which would reintroduce the blocking.
        assert base.count == 0
    finally:
        queue.empty()


def test_two_users_land_on_separate_lanes():
    # The invariant that makes head-of-line blocking impossible: two owners, two queues.
    doc_a, doc_b = _make_user_and_doc(), _make_user_and_doc()
    with get_sessionmaker()() as session:
        owner_a = session.get(Document, doc_a).user_id
        owner_b = session.get(Document, doc_b).user_id
    queue_a, queue_b = queue_for("segment", owner_a), queue_for("segment", owner_b)
    queue_a.empty()
    queue_b.empty()
    try:
        with get_sessionmaker()() as session:
            jobs.enqueue(session, doc_a, "segment", model="m", prompt_version="1")
            jobs.enqueue(session, doc_b, "segment", model="m", prompt_version="1")
        assert queue_a.name != queue_b.name
        assert queue_a.count == 1 and queue_b.count == 1
    finally:
        queue_a.empty()
        queue_b.empty()


def test_create_job_sets_queued_and_document_status():
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1")
        assert job.state == "queued"
        assert session.get(Document, doc_id).status == "segmenting"


def test_a_job_records_the_build_sha_of_the_image_that_created_it(monkeypatch):
    # WHEN a job is created, THE SYSTEM SHALL record the commit the running image was built from.
    # The prompt fingerprint covers prompt TEXT only; the templates that assemble a prompt and the
    # per-row blocks appended after that fingerprint is computed are CODE, and this is what
    # attributes them. Read from settings at call time, so a rebuilt image is picked up without a
    # code change.
    monkeypatch.setattr(get_settings(), "build_sha", "deadbee")
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1")
        assert job.build_sha == "deadbee"


def test_an_unstamped_build_is_labelled_unknown_rather_than_guessed():
    # WHEN an image is built without the GIT_SHA arg, THE SYSTEM SHALL default to "unknown". A
    # plausible-looking default would be a lie recorded as data, and the point of the stamp is that
    # it never asserts a commit it cannot know. NULL is reserved for jobs created before the column
    # existed - a different fact, and deliberately not backfilled.
    assert Settings.model_fields["build_sha"].default == "unknown"


def test_one_active_job_per_document_conflicts():
    doc_id = _make_user_and_doc()
    # First job (one "process") commits a queued row.
    with get_sessionmaker()() as session_a:
        jobs.create_job(session_a, doc_id, "segment", model="m", prompt_version="1")
    # A second create_job (a separate session/process) must conflict via the DB index -> 409.
    with get_sessionmaker()() as session_b, pytest.raises(jobs.JobConflict):
        jobs.create_job(session_b, doc_id, "summarize", model="m", prompt_version="1")


def test_run_marks_done_and_advances_status():
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id

    ran = []

    def work(session, job, report):
        report("segmenting", 1, 2)
        ran.append(True)

    _run(job_id, work)
    assert ran == [True]
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        assert session.get(Document, doc_id).status == "reviewing"


def test_run_marks_error_with_a_friendly_message():
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id

    def work(session, job, report):
        raise OcrUnavailableError("no tesseract on this host")

    _run(job_id, work)
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "error"
        assert "OCR" in job.error  # friendly, never the raw technical detail
        assert session.get(Document, doc_id).status == "error"


def test_segment_document_persists_segment_and_review_rows(monkeypatch):
    import app.services.page_text as page_text_mod
    import app.services.segment_engine as se

    # This document has no file on disk, and the page-text pass now PROPAGATES a config failure
    # instead of swallowing it (Poppler cannot open a nonexistent path). Stub the pass out: this test
    # is about row persistence, not OCR, and the OCR failure modes have their own dedicated tests.
    monkeypatch.setattr(page_text_mod, "populate_document", lambda *a, **k: 0)

    def _row(start, category):
        return {
            "start": start,
            "end": start,
            "category": category,
            "title": "A",
            "date": "-",
            "injury_date": "-",
            "flag": "-",
            "suggest_merge": False,
        }

    monkeypatch.setattr(
        se,
        "run_segmentation",
        lambda pdf_path, total_pages, progress=None, page_text_fn=None: [
            _row(1, "1"),
            _row(2, "100"),
        ],
    )
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id

    segment_document(job_id)
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        review = session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
        ).all()
        segment = session.scalars(select(SegmentRow).where(SegmentRow.job_id == job_id)).all()
        assert [r.category for r in review] == ["1", "100"]
        assert len(segment) == 2
        # include follows the category summarize_default: cat 1 on, General (100) off. Uses 100
        # rather than Depositions (9) because 9 became on-by-default on 2026-08-06 - with two
        # on-by-default categories this would pass even if include were hardcoded True.
        assert review[0].include is True
        assert review[1].include is False


def test_summarize_document_persists_summaries(monkeypatch):
    import app.services.summarize_engine as se

    monkeypatch.setattr(
        se,
        "summarize_row",
        lambda pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw: {
            "summaryTitle": "T (Pages 1-1)",
            "summaryDate": "-",
            "summaryText": "body",
            "manualCheck": "",
            "sourceText": "x",
        },
    )
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        session.add(
            ReviewRow(
                document_id=doc_id,
                idx=0,
                start=1,
                end=1,
                category="1",
                title="A",
                date="-",
                injury_date="-",
                flag="-",
                include=True,
            )
        )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id

    summarize_document(job_id)
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "done"
        assert job.progress()["attention"] is None  # a clean run carries no failure detail
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1 and summaries[0].title == "T (Pages 1-1)"
        assert summaries[0].manual_check is False


def test_summarize_document_flags_a_truncated_summary_for_manual_check(monkeypatch):
    """A reply the model cut at the token cap is stored flagged, so the reviewer sees the chip on a
    summary that stops mid-sentence instead of trusting it as complete."""
    import app.services.summarize_engine as se

    monkeypatch.setattr(
        se,
        "summarize_row",
        lambda pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw: {
            "summaryTitle": "T (Pages 1-1)",
            "summaryDate": "-",
            "summaryText": "body cut off mid-sen",
            "manualCheck": "",
            "truncated": True,
            "sourceText": "x",
        },
    )
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        session.add(
            ReviewRow(
                document_id=doc_id,
                idx=0,
                start=1,
                end=1,
                category="1",
                title="A",
                date="-",
                injury_date="-",
                flag="-",
                include=True,
            )
        )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id

    summarize_document(job_id)
    with get_sessionmaker()() as session:
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1 and summaries[0].manual_check is True


def test_summarize_document_preserves_row_order_under_parallelism(monkeypatch):
    """Rows summarize on a thread pool; an inverse-sleep mock finishes them out of order, yet the
    persisted Summary set must stay in idx (document) order."""
    import time

    import app.services.summarize_engine as se

    def fake_summarize(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        time.sleep(0.02 * (4 - int(row["start"])))  # higher start finishes first
        return {
            "summaryTitle": f"T{row['start']} (Pages {row['start']}-{row['end']})",
            "summaryDate": "-",
            "summaryText": f"body{row['start']}",
            "manualCheck": "",
            "sourceText": "x",
        }

    monkeypatch.setattr(se, "summarize_row", fake_summarize)
    doc_id = _make_user_and_doc(page_count=3)
    with get_sessionmaker()() as session:
        for idx in range(3):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=idx + 1,
                    end=idx + 1,
                    category="1",
                    title="A",
                    date="-",
                    injury_date="-",
                    flag="-",
                    include=True,
                )
            )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id

    summarize_document(job_id)
    with get_sessionmaker()() as session:
        summaries = session.scalars(
            select(Summary).where(Summary.document_id == doc_id).order_by(Summary.idx)
        ).all()
        assert [s.idx for s in summaries] == [0, 1, 2]
        assert [s.title for s in summaries] == [
            "T1 (Pages 1-1)",
            "T2 (Pages 2-2)",
            "T3 (Pages 3-3)",
        ]


def test_enqueue_dispatches_to_the_right_queue():
    doc_id = _make_user_and_doc()
    queue = lanes("segment")
    queue.empty()
    try:
        with get_sessionmaker()() as session:
            jobs.enqueue(session, doc_id, "segment", model="m", prompt_version="1")
        assert queue.count == 1
        assert queue.jobs[0].func_name.endswith("segment_document")
    finally:
        queue.empty()  # don't leave a job for a real worker to pick up


def test_recover_orphans_interrupts_a_dead_job():
    """A DB job stuck 'running' with no RQ counterpart (its worker died) is interrupted."""
    from app.worker.recovery import recover_orphans

    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1")
        job.state = "running"  # a (now-dead) worker had started it; no RQ record remains
        session.commit()
        job_id = job.id

    with get_sessionmaker()() as session:
        assert recover_orphans(session) >= 1
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "interrupted"
        assert session.get(Document, doc_id).status == "interrupted"


def test_recover_orphans_leaves_a_healthy_job():
    """A DB job whose RQ counterpart is still queued (a live worker will run it) is left alone."""
    from app.worker.recovery import recover_orphans

    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id

    # Any real queue will do: RQ job ids are GLOBAL (rq:job:{id}), not per-queue, so recovery
    # correlates by id and never enumerates queues - which is why per-user lanes leave it untouched.
    queue = queue_for("segment")
    # A real RQ job whose id matches the DB job -> recover_orphans sees it queued (healthy).
    queue.enqueue("app.worker.tasks.segment_document", job_id, job_id=str(job_id))
    try:
        with get_sessionmaker()() as session:
            recover_orphans(session)
        with get_sessionmaker()() as session:
            assert session.get(Job, job_id).state == "queued"  # untouched
    finally:
        queue.empty()


# --- resumable summarize (item 7) ---------------------------------------------------------------


def _doc_with_summarize_rows(n: int, category: str = "1") -> tuple[str, int]:
    """A document with n included ReviewRows (start=end=idx+1) + a queued summarize job."""
    doc_id = _make_user_and_doc(page_count=n)
    with get_sessionmaker()() as session:
        for idx in range(n):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=idx + 1,
                    end=idx + 1,
                    category=category,
                    title="A",
                    date="-",
                    injury_date="-",
                    flag="-",
                    include=True,
                )
            )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id
    return doc_id, job_id


def _ok_output(row) -> dict:
    return {
        "summaryTitle": f"T{row['start']} (Pages {row['start']}-{row['end']})",
        "summaryDate": "-",
        "summaryText": f"body{row['start']}",
        "manualCheck": "",
        "sourceText": "x",
    }


def test_the_worker_hands_each_row_the_records_other_diagnostic_studies(monkeypatch):
    """E-08: WHEN a record holds standalone diagnostic studies, THE SYSTEM SHALL pass their titles
    with every row it summarizes, so a sub-document carrying a records review can tell which studies
    are already summarized in their own right. A study is never listed against itself."""
    import app.services.summarize_engine as se

    seen: dict[int, list] = {}

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        seen[int(row["start"])] = standalone_studies
        return _ok_output(row)

    monkeypatch.setattr(se, "summarize_row", fake)

    doc_id = _make_user_and_doc(page_count=3)
    with get_sessionmaker()() as session:
        for idx, (category, title) in enumerate(
            [("3", "MRI OF THE KNEE"), ("3", "CT OF THE HEAD"), ("13", "QME REPORT")]
        ):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=idx + 1,
                    end=idx + 1,
                    category=category,
                    title=title,
                    date="-",
                    injury_date="-",
                    flag="-",
                    include=True,
                )
            )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id

    summarize_document(job_id)

    assert [study["title"] for study in seen[3]] == ["MRI OF THE KNEE", "CT OF THE HEAD"]
    assert [study["title"] for study in seen[1]] == ["CT OF THE HEAD"]  # itself excluded


def test_summarize_persists_per_row_and_reuses_done_on_rerun(monkeypatch):
    """Skip-done by row identity: an existing summary is reused (its edit preserved), only the
    missing row is generated, and it is positioned to the current row order."""
    import app.services.summarize_engine as se

    calls: list[int] = []

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        calls.append(int(row["start"]))
        return _ok_output(row)

    monkeypatch.setattr(se, "summarize_row", fake)

    doc_id, job_id = _doc_with_summarize_rows(2)
    with get_sessionmaker()() as session:  # a prior run already summarized row identity (1,1,"1")
        session.add(
            Summary(
                document_id=doc_id,
                job_id=job_id,
                idx=0,
                title="OLD",
                date="-",
                text="old body",
                edited_text="reviewer edit",
                row_start=1,
                row_end=1,
                row_category="1",
            )
        )
        session.commit()

    summarize_document(job_id)
    assert calls == [2]  # row 1 reused (skipped); only row 2 generated
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        summaries = session.scalars(
            select(Summary).where(Summary.document_id == doc_id).order_by(Summary.idx)
        ).all()
        assert len(summaries) == 2
        assert summaries[0].title == "OLD"  # reused, not regenerated
        assert summaries[0].edited_text == "reviewer edit"  # edit preserved
        assert summaries[0].idx == 0


def test_summarize_pauses_and_schedules_resume_on_transient(monkeypatch):
    """Sustained transient 429 -> stop, keep progress, schedule a delayed resume, state=paused
    (NOT error); the document stays in-flight ('summarizing')."""
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    monkeypatch.setattr(se, "summarize_row", fake)

    scheduled: dict = {}

    class _FakeQueue:
        def enqueue_in(self, td, fn, arg, job_timeout=None, on_stopped=None, on_failure=None):
            scheduled["delay"] = td.total_seconds()
            scheduled["arg"] = arg
            # A resumed summarize is the longest-running job here and the likeliest to be force
            # stopped, so the resume dispatch MUST carry the finalizers too.
            scheduled["on_stopped"] = on_stopped
            scheduled["on_failure"] = on_failure
            return type("_J", (), {"id": "rq-resume-1"})()

    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _FakeQueue())
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 1)
    monkeypatch.setattr(get_settings(), "summarize_resume_delay", 60)

    doc_id, job_id = _doc_with_summarize_rows(3)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "paused"
        assert job.rq_job_id == "rq-resume-1"
        assert session.get(Document, doc_id).status == "summarizing"
        assert session.scalars(select(Summary).where(Summary.document_id == doc_id)).all() == []
    assert scheduled["delay"] == 60
    assert str(scheduled["arg"]) == str(job_id)
    # Without these the resumed run is the one job a force stop cannot finalize, leaving the
    # document wedged until the API restarts.
    assert scheduled["on_stopped"] is not None
    assert scheduled["on_failure"] is not None


class _NoopQueue:
    """A queue_for stand-in for tests where SCHEDULING a resume is the wrong outcome: a wrong
    result then fails on the assertion below rather than on a missing Redis."""

    def enqueue_in(self, td, fn, arg, job_timeout=None, on_stopped=None, on_failure=None):
        return type("_J", (), {"id": "rq-resume-unexpected"})()


def test_summarize_gives_up_when_no_row_ever_succeeds(monkeypatch):
    """A model that admits nothing ends the job in ONE pass instead of pausing into an endless
    resume cycle. Zero successes is the discriminator: it is what 0/8 admission looks like, and it
    is what a transient blip with some rows getting through does not."""
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.errors import GENERIC_USER_MESSAGE
    from app.worker import tasks as tasks_mod

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _NoopQueue())
    # A high pause threshold so the GIVE-UP is what ends this job, not the pre-existing pause path.
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 99)
    monkeypatch.setattr(get_settings(), "summarize_giveup_after_failures", 3)

    doc_id, job_id = _doc_with_summarize_rows(8)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "needs_attention"
        # The reviewer must learn the AI service was unavailable, not read the generic message that
        # made job 1000173 unreadable for 96 minutes.
        assert "AI service" in (job.error or "")
        assert GENERIC_USER_MESSAGE not in (job.error or "")
        assert session.scalars(select(Summary).where(Summary.document_id == doc_id)).all() == []


def test_summarize_does_not_give_up_once_a_row_has_succeeded(monkeypatch):
    """WHILE at least one row has succeeded, sustained transient failures stay a PAUSE: rows are
    getting through, so the model is not refusing everything and the rest deserve their retry."""
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        if int(row["start"]) == 1:
            return _ok_output(row)
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    scheduled: dict = {}

    class _FakeQueue:
        def enqueue_in(self, td, fn, arg, job_timeout=None, on_stopped=None, on_failure=None):
            scheduled["delay"] = td.total_seconds()
            return type("_J", (), {"id": "rq-resume-1"})()

    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _FakeQueue())
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 99)
    monkeypatch.setattr(get_settings(), "summarize_giveup_after_failures", 3)

    doc_id, job_id = _doc_with_summarize_rows(8)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "paused"  # NOT needs_attention: one row proved the model answers
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1 and summaries[0].row_start == 1  # the good row is kept
    assert scheduled["delay"] == get_settings().summarize_resume_delay


def test_giving_up_stops_submitting_the_remaining_rows(monkeypatch):
    """Giving up must stop WORK, not just relabel the outcome: the point is to spend three calls
    finding out the model is refusing, not a whole document's worth."""
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    calls = {"n": 0}

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        calls["n"] += 1
        # A real refusal costs a round trip (~0.1s even when Vertex refuses at admission). Raising
        # INSTANTLY would let a one-worker pool run all twelve futures before the drain loop reads the
        # third result, so the test would measure the scheduler's head start rather than the give-up.
        time.sleep(0.02)
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _NoopQueue())
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 99)
    monkeypatch.setattr(get_settings(), "summarize_giveup_after_failures", 3)
    monkeypatch.setattr(get_settings(), "pipeline_workers", 1)

    _doc_id, job_id = _doc_with_summarize_rows(12)
    summarize_document(job_id)

    # Not an exact count: a row can already be in flight when the third failure is drained, so the
    # bound is what matters - far fewer than the twelve a full pass would cost.
    assert 3 <= calls["n"] < 12


def test_giving_up_wins_over_pausing_at_the_default_thresholds(monkeypatch):
    """Both dials ship at 3, so a total refusal satisfies each at the same moment. Ending the job
    must win: pausing would auto-resume into the same wall, which IS the 96-minute failure."""
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    settings = get_settings()
    # State the premise rather than assume it: if a later change splits these defaults apart, this
    # test should say so instead of passing for the wrong reason.
    assert settings.summarize_pause_after == settings.summarize_giveup_after_failures == 3

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _NoopQueue())

    _doc_id, job_id = _doc_with_summarize_rows(8)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "needs_attention"


def test_a_success_still_running_when_the_pause_trips_is_not_thrown_away(monkeypatch):
    """The sibling of the give-up race, reachable IMMEDIATELY AFTER it.

    Once `giveup_candidate` is set the give-up guard can never fire again, and it continues without
    resetting `consecutive_transient` - which is already at the threshold, because both dials ship at
    3. So the very next transient failure lands in the pause branch, and that branch used to `break`,
    abandoning every row still RUNNING. A success among them was never read, `generated` stayed 0, the
    post-loop promotion fired, and `giveup_exc` is checked BEFORE `should_pause` - so a document where
    a row DID summarize ended as needs_attention instead of pausing and retrying the skipped rows.

    DETERMINISTIC BY CONSTRUCTION. Two earlier attempts were not, and each failed for its own reason
    worth recording:

    * a 0.4s sleep on the success passed on the unfixed code about half the time - the timing
      dependence this whole area exists to remove;
    * gating the success on the main thread's progress alone DEADLOCKED, because the give-up branch
      cancels every not-yet-STARTED row, so the second failing row was cancelled before it could run
      and the second failure never arrived.

    Hence the barrier: every row is held until all four are in flight, so `cancel()` can reach none of
    them. The failures then raise immediately, and the success is released only once the main thread
    has processed two of them - it calls `reason_for` once per failure, before either the give-up or
    the pause check. So the success is in flight at exactly the decision point and never read before
    it. Whether it is read AT ALL is precisely what `break` versus `continue` decides.
    """
    import threading

    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    monkeypatch.setattr(get_settings(), "summarize_giveup_after_failures", 1)
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 1)
    monkeypatch.setattr(get_settings(), "pipeline_workers", 4)

    all_in_flight = threading.Barrier(4, timeout=10)
    decision_reached = threading.Event()  # the main thread has processed two failures
    real_reason_for = tasks_mod.reason_for
    seen = []

    def gated_reason_for(exc):
        seen.append(1)
        if len(seen) >= 2:
            decision_reached.set()
        return real_reason_for(exc)

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        all_in_flight.wait()  # nothing is cancellable once every row holds a lane
        if row["start"] == 1:  # the one row that answers
            assert decision_reached.wait(timeout=10), "the two failures were never processed"
            return _ok_output(row)
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    monkeypatch.setattr(tasks_mod, "reason_for", gated_reason_for)
    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _NoopQueue())

    _doc_id, job_id = _doc_with_summarize_rows(4)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "paused", (
            "a row summarized, so the job must pause and retry the rest rather than end - "
            f"got {job.state!r}"
        )
        stored = session.query(Summary).filter(Summary.document_id == _doc_id).all()
        assert len(stored) == 1, (
            f"the successful row must be committed, not discarded; stored {len(stored)}"
        )


def test_summarize_needs_attention_on_permanent_keeps_partial(monkeypatch):
    """A permanent per-row failure (empty OCR) ends the job 'needs_attention' naming the row,
    while every readable row is still persisted."""
    import app.services.summarize_engine as se

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        if int(row["start"]) == 1:
            raise EmptyExtractionError("no OCR text for pages 1-1")
        return _ok_output(row)

    monkeypatch.setattr(se, "summarize_row", fake)

    doc_id, job_id = _doc_with_summarize_rows(2)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "needs_attention"
        assert session.get(Document, doc_id).status == "needs_attention"
        assert "could not be summarized" in (job.error or "")
        assert job.attention and job.attention["rows"][0]["pages"] == "1-1"
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1 and summaries[0].row_start == 2  # readable row kept


def test_job_progress_exposes_attention_rows(monkeypatch):
    """H1: the status payload (Job.progress) surfaces the per-row failure detail so the UI can list
    + highlight exactly which sub-documents failed, with the reason. (Worker persistence itself is
    covered by test_summarize_needs_attention_on_permanent_keeps_partial.)"""
    import app.services.summarize_engine as se

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        if int(row["start"]) == 1:
            raise EmptyExtractionError("no OCR text for pages 1-1")
        return _ok_output(row)

    monkeypatch.setattr(se, "summarize_row", fake)

    doc_id, job_id = _doc_with_summarize_rows(2)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        progress = session.get(Job, job_id).progress()
        assert progress["state"] == "needs_attention"
        assert progress["attention"]["rows"][0]["pages"] == "1-1"
        assert "readable text" in progress["attention"]["rows"][0]["reason"].lower()


def test_summarize_pauses_when_pool_times_out(monkeypatch):
    """A stalled summarize pool (rows neither succeed nor error within pool_timeout) pauses +
    schedules a resume rather than hanging; the outstanding rows retry on the next run."""
    import time

    import app.services.summarize_engine as se

    from app.worker import tasks as tasks_mod

    monkeypatch.setattr(
        se,
        "summarize_row",
        lambda pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw: time.sleep(
            1.5
        ),
    )

    scheduled: dict = {}

    class _FakeQueue:
        def enqueue_in(self, td, fn, arg, job_timeout=None, on_stopped=None, on_failure=None):
            scheduled["arg"] = arg
            return type("_J", (), {"id": "rq-resume-timeout"})()

    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _FakeQueue())
    settings = get_settings()  # shrink the size-aware pool budget to ~1s
    monkeypatch.setattr(settings, "job_timeout", 1)
    monkeypatch.setattr(settings, "job_timeout_per_page", 0.0)
    monkeypatch.setattr(settings, "future_timeout_margin_seconds", 0)

    doc_id, job_id = _doc_with_summarize_rows(2)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "paused"
        assert job.rq_job_id == "rq-resume-timeout"
        assert session.get(Document, doc_id).status == "summarizing"
    assert str(scheduled["arg"]) == str(job_id)


def test_second_job_conflicts_while_paused(monkeypatch):
    """A paused summarize job is in-flight: a second job for the same document must 409."""
    doc_id, job_id = _doc_with_summarize_rows(1)
    with get_sessionmaker()() as session:
        session.get(Job, job_id).state = "paused"
        session.commit()
    with get_sessionmaker()() as session, pytest.raises(jobs.JobConflict):
        jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1")


def test_classify_document_sets_each_rows_category(monkeypatch):
    """P6: classify_document classifies each seeded row (classifier mocked) - no always-category_01."""
    import app.services.classification as classification
    from app.services.classification import Classification
    from app.worker.tasks import classify_document

    # Classify to Depositions (9), which is off-by-default, so include must flip to False.
    monkeypatch.setattr(
        classification,
        "classify",
        lambda title, page_text=None: Classification("100", "high", "rules", needs_review=False),
    )
    doc_id = _make_user_and_doc(page_count=2)
    with get_sessionmaker()() as session:
        for idx in range(2):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=idx + 1,
                    end=idx + 1,
                    category="100",
                    title="-",
                    date="-",
                    injury_date="-",
                    flag="-",
                    include=True,
                )
            )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "classify", model="m", prompt_version="1").id

    classify_document(job_id)
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        rows = session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
        ).all()
        assert [r.category for r in rows] == ["100", "100"]  # per-row classification applied
        # include RE-DERIVED from the new category, which is the point: the rows were seeded
        # include=True and General (100) is off-by-default, so a False here can only come from
        # re-derivation. Was Depositions (9) until it became on-by-default on 2026-08-06.
        assert all(r.include is False for r in rows)


def _fake_row_text(texts, fail_on=()):
    """Stand-in for page_text.get_row_text_with_report, keyed on a row's first page.

    Dedup reads row text from the page store now rather than OCRing it directly, so the seam these
    tests stub moved. Same (text, report) contract, because that contract is exactly what dedup
    depends on: an errored page and a blank page mean different things to a reviewer.
    """

    def fake(session, document_id, pages, pdf_path=None, **kwargs):
        pages = list(pages)
        first = pages[0]
        if first in fail_on:
            raise RuntimeError("ocr boom")
        text = texts.get(first, "")
        blank = [] if text.strip() else list(pages)
        return text, {"pages": list(pages), "errored": [], "blank": blank}

    return fake


def _fake_ocr(texts, fail_on=()):
    """Stand-in for ocr.extract_pages_with_report keyed on a row's first page.

    Returns the (text, report) pair the real helper does; a page in ``fail_on`` raises, and a page
    absent from ``texts`` reads blank - the two outcomes dedup now has to tell apart.
    """

    def fake(path, pages, **kwargs):
        first = pages[0]
        if first in fail_on:
            raise RuntimeError("ocr boom")
        text = texts.get(first, "")
        blank = [] if text.strip() else list(pages)
        return text, {"pages": len(pages), "errored": [], "blank": blank}

    return fake


def test_dedup_document_clusters_confirmed_duplicates(monkeypatch):
    """dedup_document OCRs each row once, clusters near-identical text, and stores a shared
    dupe_group for the confirmed copies (OCR + confirm are mocked; cluster_rows runs for real)."""
    doc_id = _make_user_and_doc(page_count=3)
    with get_sessionmaker()() as session:
        for idx, (start, end) in enumerate([(1, 1), (2, 2), (3, 3)]):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=start,
                    end=end,
                    category="1",
                    title="T",
                    date="-",
                    injury_date="-",
                    flag="-",
                    include=True,
                )
            )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "dedup", model="m", prompt_version="1").id

    # Pages 1 and 2 are the same document; page 3 is distinct.
    texts = {
        1: "alpha beta gamma delta epsilon zeta eta theta",
        2: "alpha beta gamma delta epsilon zeta eta theta",
        3: "completely different words nothing shared at all here",
    }
    monkeypatch.setattr("app.services.page_text.get_row_text_with_report", _fake_row_text(texts))
    # Trust the algorithmic candidate (no Vertex call in the test).
    monkeypatch.setattr("app.services.dedup.confirm_cluster", lambda members, model=None: members)

    dedup_document(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        rows = {
            r.idx: r
            for r in session.scalars(
                select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
            ).all()
        }
    assert rows[0].source_text and rows[1].source_text  # OCR text persisted per row
    assert rows[0].dupe_group is not None
    assert rows[0].dupe_group == rows[1].dupe_group  # the two copies share a group
    assert rows[2].dupe_group is None  # the distinct document is not grouped


def test_dedup_document_skips_ocred_rows_and_survives_an_ocr_failure(monkeypatch):
    """dedup_document reuses stored source_text (no re-OCR) and tolerates a per-row OCR failure.

    The confirm step no longer decides this case: at similarity 1.0 the text has already answered the
    question, so the cluster is kept without a model call (see the model-override test below).
    """
    doc_id = _make_user_and_doc(page_count=3)
    with get_sessionmaker()() as session:
        # row0 already has OCR text (should be skipped); row1's page will fail OCR; row2 is normal
        # and shares row0's text so they form a candidate cluster.
        session.add(
            ReviewRow(
                document_id=doc_id,
                idx=0,
                start=1,
                end=1,
                category="1",
                title="T",
                date="-",
                injury_date="-",
                flag="-",
                include=True,
                source_text="alpha beta gamma delta epsilon",
            )
        )
        for idx, (start, end) in ((1, (2, 2)), (2, (3, 3))):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=start,
                    end=end,
                    category="1",
                    title="T",
                    date="-",
                    injury_date="-",
                    flag="-",
                    include=True,
                )
            )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "dedup", model="m", prompt_version="1").id

    ocr_calls = []
    same = "alpha beta gamma delta epsilon"

    def fake_row_text(session, document_id, pages, pdf_path=None, **kwargs):
        pages = list(pages)
        ocr_calls.append(pages[0])
        if pages[0] == 2:
            raise RuntimeError("ocr boom")  # row1: per-row OCR failure is tolerated
        return same, {"pages": pages, "errored": [], "blank": []}

    # Dedup reads row text from the page store now, so that is the seam to stub.
    monkeypatch.setattr("app.services.page_text.get_row_text_with_report", fake_row_text)
    confirm_calls = []
    monkeypatch.setattr(
        "app.services.dedup.confirm_cluster",
        lambda members, model=None: confirm_calls.append(members) or members,
    )

    dedup_document(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        rows = {
            r.idx: r
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        }
    assert 1 not in ocr_calls  # row0 already had source_text -> its page was not re-OCR'd
    assert rows[0].source_text == same  # unchanged
    assert rows[1].source_text == ""  # OCR failed -> empty, run still completed
    # row1 has no text, so it cannot match anything; rows 0 and 2 are identical and cluster.
    assert rows[1].dupe_group is None
    assert rows[0].dupe_group == rows[2].dupe_group is not None
    assert confirm_calls == []  # similarity 1.0 settled it without spending a Vertex call


def test_dedup_document_rejects_a_form_series_before_spending_a_confirm_call(monkeypatch):
    """WHEN candidate members share neither a date nor a title and their content is not near-identical,
    THE SYSTEM SHALL drop the candidate WITHOUT calling the model.

    This is the dominant false positive on real records: a recurring form whose members span many
    dates. Gating before the call improves precision and removes a Vertex call per rejected cluster.
    """
    doc_id = _make_user_and_doc(page_count=2)
    shared = "a1 b2 c3 d4 e5 f6 g7 h8 i9 j0"
    job_id = _dedup_rows(
        doc_id,
        [
            (1, 1, True, False, None, f"{shared} {'x' * 40}"),
            (2, 2, True, False, None, f"{shared} {'y' * 40}"),
        ],
        dates=["05/08/2022", "06/12/2022"],  # different visits
        titles=["Work Status Report", "Progress Note"],  # and different documents
    )
    confirm_calls = []
    monkeypatch.setattr(
        "app.services.dedup.confirm_cluster",
        lambda members, model=None: confirm_calls.append(members) or members,
    )

    dedup_document(job_id)

    rows = _rows_by_idx(doc_id)
    assert all(r.dupe_group is None for r in rows.values())
    assert confirm_calls == []  # rejected by metadata, so no quota was spent on it


def test_dedup_document_still_honours_a_confirm_rejection_below_the_override(monkeypatch):
    """The model keeps its say on the ambiguous cluster: same date AND title (so the accuracy gate
    passes on metadata) but middling content similarity, which is exactly where a form filled out
    twice on one day is indistinguishable from a re-scan without reading it."""
    doc_id = _make_user_and_doc(page_count=2)
    shared = "a1 b2 c3 d4 e5 f6 g7 h8 i9 j0"
    job_id = _dedup_rows(
        doc_id,
        [
            (1, 1, True, False, None, f"{shared} {'x' * 40}"),
            (2, 2, True, False, None, f"{shared} {'y' * 40}"),
        ],
        dates=["05/08/2022", "05/08/2022"],
        titles=["Work Status Report", "Work Status Report"],
    )
    confirm_calls = []
    monkeypatch.setattr(
        "app.services.dedup.confirm_cluster",
        lambda members, model=None: confirm_calls.append(members) or [],
    )

    dedup_document(job_id)

    rows = _rows_by_idx(doc_id)
    assert len(confirm_calls) == 1  # the gate let it through to the model
    assert all(r.dupe_group is None for r in rows.values())  # and the model's rejection stands


def test_dedup_document_counts_rows_it_could_not_read(monkeypatch):
    """WHEN a row's pages yield no text, THE SYSTEM SHALL leave it ungrouped and record that it was
    never compared - empty text scores 0.0 against everything, so such a row is invisible to the
    check rather than merely unmatched (measured live at 18 of 91 rows)."""
    doc_id = _make_user_and_doc(page_count=3)
    same = "alpha beta gamma delta epsilon zeta eta theta"
    job_id = _dedup_rows(
        doc_id,
        [
            (1, 1, True, False, None, None),
            (2, 2, True, False, None, None),
            (3, 3, True, False, None, None),
        ],
    )
    # Page 3 reads clean but carries no words (a film or separator sheet).
    monkeypatch.setattr(
        "app.services.page_text.get_row_text_with_report", _fake_row_text({1: same, 2: same})
    )
    monkeypatch.setattr("app.services.dedup.confirm_cluster", lambda members, model=None: members)

    dedup_document(job_id)

    rows = _rows_by_idx(doc_id)
    assert rows[0].dupe_group == rows[1].dupe_group is not None
    assert rows[2].source_text == ""  # persisted as read-and-empty, not left NULL
    assert rows[2].dupe_group is None


def _dedup_rows(doc_id, specs, dates=None, titles=None):
    """Seed ReviewRows from (start, end, include, dismissed, group, text) tuples + a dedup job id.

    ``dates`` / ``titles`` override the per-row metadata, which the accuracy gate reads: the default
    "-" date is UNKNOWN and never matches, so a default-seeded cluster is judged on content alone.
    """
    with get_sessionmaker()() as session:
        for idx, (start, end, include, dismissed, group, text) in enumerate(specs):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=start,
                    end=end,
                    category="1",
                    title=titles[idx] if titles else "T",
                    date=dates[idx] if dates else "-",
                    injury_date="-",
                    flag="-",
                    include=include,
                    dupe_dismissed=dismissed,
                    dupe_group=group,
                    source_text=text,
                )
            )
        session.commit()
        return jobs.create_job(session, doc_id, "dedup", model="m", prompt_version="1").id


def _rows_by_idx(doc_id):
    with get_sessionmaker()() as session:
        return {
            r.idx: r
            for r in session.scalars(
                select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
            ).all()
        }


def test_dedup_document_skips_excluded_rows_and_keeps_an_unchanged_dismissal(monkeypatch):
    """Scope is the rows the reviewer CHECKED - an excluded copy is not read and not clustered - and a
    dismissed cluster whose copies are unchanged comes back still dismissed, so a settled "not
    duplicates" stays quiet.

    Inverted on 2026-08-06. Dedup used to cover every row, on the reasoning that General and
    Depositions are unchecked by default and that is where re-scanned letters live. Depositions became
    checked by default (PR #82), and the residual General gap was accepted: a row the reviewer
    excluded will not be summarized, so a duplicate among excluded rows cannot reach a client."""
    doc_id = _make_user_and_doc(page_count=5)
    same = "alpha beta gamma delta epsilon zeta eta theta"
    dismissed_text = "kappa lambda mu nu xi omicron pi rho sigma"
    job_id = _dedup_rows(
        doc_id,
        [
            (1, 1, True, False, None, None),
            (2, 2, False, False, None, None),  # excluded copy of page 1
            (3, 3, True, True, 7, dismissed_text),  # dismissed pair, member A
            (4, 4, True, True, 7, dismissed_text),  # dismissed pair, member B
            (5, 5, True, False, None, None),  # distinct
        ],
    )
    texts = {1: same, 2: same, 5: "completely different words nothing shared at all here"}
    monkeypatch.setattr("app.services.page_text.get_row_text_with_report", _fake_row_text(texts))
    monkeypatch.setattr("app.services.dedup.confirm_cluster", lambda members, model=None: members)

    dedup_document(job_id)

    rows = _rows_by_idx(doc_id)
    # The excluded copy is left entirely alone: not clustered, and not even OCR'd - its source_text
    # stays None, which is what proves the row was skipped rather than read and found unique.
    assert rows[1].include is False
    assert rows[1].dupe_group is None
    assert rows[1].source_text is None
    # Its included twin therefore has nothing to pair with and is not a duplicate either.
    assert rows[0].dupe_group is None
    assert rows[0].dupe_dismissed is False
    # The dismissed pair was re-examined and re-dismissed (same two page ranges).
    assert rows[2].dupe_group == rows[3].dupe_group is not None
    assert rows[2].dupe_dismissed is True and rows[3].dupe_dismissed is True
    assert rows[4].dupe_group is None
    # Nothing survives a re-check now, so group ids restart from 1 rather than climbing forever.
    assert min(r.dupe_group for r in rows.values() if r.dupe_group) == 1


def test_dedup_document_resurfaces_a_dismissed_cluster_that_gained_a_copy(monkeypatch):
    """WHEN a dismissed cluster gains a copy, the reviewer's earlier "not duplicates" answered a
    different question, so the new cluster must come back for review."""
    doc_id = _make_user_and_doc(page_count=3)
    same = "kappa lambda mu nu xi omicron pi rho sigma"
    job_id = _dedup_rows(
        doc_id,
        [
            (1, 1, True, True, 4, same),  # previously dismissed as a PAIR
            (2, 2, True, True, 4, same),
            (3, 3, True, False, None, same),  # a third copy has appeared since
        ],
    )
    monkeypatch.setattr("app.services.dedup.confirm_cluster", lambda members, model=None: members)

    dedup_document(job_id)

    rows = _rows_by_idx(doc_id)
    assert rows[0].dupe_group == rows[1].dupe_group == rows[2].dupe_group is not None
    assert [rows[i].dupe_dismissed for i in (0, 1, 2)] == [False, False, False]


def test_dedup_document_failure_leaves_the_previous_clusters_intact(monkeypatch):
    """WHEN a dedup run dies before it finishes, the clusters already on the tab SHALL survive -
    clearing them mid-run used to leave the reviewer looking at "No duplicates"."""
    doc_id = _make_user_and_doc(page_count=2)
    same = "alpha beta gamma delta epsilon zeta eta theta"
    job_id = _dedup_rows(
        doc_id,
        [(1, 1, True, False, 3, same), (2, 2, True, False, 3, same)],
    )

    def boom(*a, **k):
        raise RuntimeError("clustering blew up")

    monkeypatch.setattr("app.services.dedup.cluster_rows", boom)

    dedup_document(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "error"
    rows = _rows_by_idx(doc_id)
    assert rows[0].dupe_group == rows[1].dupe_group == 3  # untouched
    assert rows[0].source_text and rows[1].source_text


def test_dedup_document_stores_the_cluster_similarity(monkeypatch):
    """The score the clusterer computes is persisted on every member, so the API can report WHY rows
    clustered: near-identical copies score ~1.0."""
    doc_id = _make_user_and_doc(page_count=3)
    same = "alpha beta gamma delta epsilon zeta eta theta"
    job_id = _dedup_rows(
        doc_id,
        [
            (1, 1, True, False, None, same),
            (2, 2, True, False, None, same),
            (3, 3, True, False, None, "completely different words nothing shared at all"),
        ],
    )
    monkeypatch.setattr("app.services.dedup.confirm_cluster", lambda members, model=None: members)

    dedup_document(job_id)

    rows = _rows_by_idx(doc_id)
    assert rows[0].dupe_similarity == rows[1].dupe_similarity == 1.0
    assert rows[2].dupe_similarity is None  # a singleton carries no cluster score


def test_a_keep_one_resolution_is_not_reopened_by_a_re_check(monkeypatch):
    """A re-check must not reopen a keep-one resolution.

    Rewritten 2026-08-06 with the scope change. keep_one sets include=False on every copy it did not
    keep, and dedup now reads only included rows - so the collapsed cluster has exactly one member
    left in scope, a cluster of one is not a duplicate set, and it simply does not come back. The
    reviewer's answer stands.

    This is a stronger guarantee than the old one, not a weaker one: previously the cluster re-formed
    every run and relied on dupe_primary surviving to present as resolved. Now there is nothing to
    re-form. Re-including the excluded copy puts it back in scope, which is the correct way to reopen
    the question deliberately."""
    doc_id = _make_user_and_doc(page_count=2)
    with get_sessionmaker()() as session:
        for idx, (include, primary) in enumerate([(True, True), (False, False)]):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=idx + 1,
                    end=idx + 1,
                    category="1",
                    title="T",
                    date="-",
                    injury_date="-",
                    flag="-",
                    include=include,
                    dupe_group=1,
                    dupe_primary=primary,
                    source_text="alpha beta gamma delta epsilon zeta eta theta",
                )
            )
        session.commit()
        job_id = jobs.create_job(session, doc_id, "dedup", model="m", prompt_version="1").id

    monkeypatch.setattr("app.services.dedup.confirm_cluster", lambda members, model=None: members)

    dedup_document(job_id)

    with get_sessionmaker()() as session:
        rows = {
            r.idx: r
            for r in session.scalars(
                select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
            ).all()
        }
    # The excluded copy was out of scope, so the pair cannot re-form and the resolved cluster stays
    # closed. Both rows end ungrouped rather than re-clustered.
    assert rows[0].dupe_group is None
    assert rows[1].dupe_group is None
    # The kept copy keeps its include; the resolution is not undone.
    assert rows[0].include is True
    assert rows[1].include is False


def _dedup_jobs(doc_id):
    with get_sessionmaker()() as session:
        return session.scalars(
            select(Job).where(Job.document_id == doc_id, Job.kind == "dedup")
        ).all()


def test_identify_does_not_start_a_duplicate_check(monkeypatch):
    """WHEN segment or classify completes, THE SYSTEM SHALL NOT enqueue a dedup job.

    Replaces three test_chain_dedup_* tests deleted with `_chain_dedup` on 2026-08-06. Duplicate
    detection is now started by the reviewer from the Duplicates tab, once they have chosen which
    sub-documents to summarize - checking documents nobody will summarize is work spent for nothing,
    and running it automatically means running it before the choice has been made.
    """
    import app.services.page_text as page_text_mod
    import app.services.segment_engine as se

    # This document has no file on disk, and the page-text pass now PROPAGATES a config failure
    # instead of swallowing it (Poppler cannot open a nonexistent path). Stub the pass out: this test
    # is about which jobs get enqueued, not OCR, and the OCR failure modes have their own dedicated tests.
    monkeypatch.setattr(page_text_mod, "populate_document", lambda *a, **k: 0)

    monkeypatch.setattr(se, "get_genai_client", lambda: None)
    monkeypatch.setattr(se, "byte_budgeted_windows", lambda *a, **k: [(1, 2)])
    monkeypatch.setattr(
        se,
        "_window_rows",
        lambda pdf_path, ws, we, client: [
            dict(start=1, end=2, title="A", date="-", injury_date="-", flag="-")
        ],
    )

    # The real _categorize SETS row["category"]; a pass-through stub leaves the key absent and the
    # persistence step downstream raises KeyError, failing the job for the wrong reason.
    # Three-arg since the page-text store landed: _categorize(pdf_path, row, page_text_fn). **_kw so a
    # later parameter cannot turn this into a TypeError inside a pool worker, where it surfaces as an
    # opaque job failure rather than a signature error.
    def _fake_categorize(pdf_path, row, *_a, **_kw):
        row["category"] = "1"
        return row

    monkeypatch.setattr(se, "_categorize", _fake_categorize)
    monkeypatch.setattr(se.get_settings(), "verify_merge", False, raising=False)

    doc_id = _make_user_and_doc(page_count=2)
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id

    segment_document(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"  # the identify itself still succeeded
    assert _dedup_jobs(doc_id) == []


# --- Finalizers: the parent worker writing a terminal state its dead work-horse could not ---------
#
# The gap these close, found live 2026-08-03: a force-stopped segment job sat "running" for 30
# minutes with RQ reporting STOPPED. The UI kept its bar spinning and the one-active-job index
# refused every new run on that document. `recover_orphans` would have reaped it, but it only runs
# at API startup, so the document stayed wedged until someone restarted the API.


class _FakeRQJob:
    """Only what the callbacks read. `id` differs from `args[0]` on purpose - that is the resumed
    summarize case, where correlating by RQ id instead of the DB job id would finalize nothing."""

    def __init__(self, db_job_id, rq_id="rq-fresh-id"):
        self.args = [db_job_id]
        self.id = rq_id


def _running_job(kind: str = "segment") -> tuple[str, int]:
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, kind, model="m", prompt_version="1")
        job.state = "running"
        session.commit()
        return doc_id, job.id


def test_stopped_callback_finalizes_a_force_stopped_job():
    """Force stop kills the work-horse; the PARENT worker must write the terminal state."""
    from app.worker.finalizers import on_job_stopped

    doc_id, job_id = _running_job("segment")
    on_job_stopped(_FakeRQJob(job_id), None)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "cancelled"
        assert job.stage == "cancelled"
        assert job.finished_at is not None
        assert job.error is None  # a stop is not a fault
        # Same terminal state the COOPERATIVE stop writes, so the two paths cannot drift.
        assert session.get(Document, doc_id).status == jobs.STATUS_ON_CANCEL["segment"]


def test_stopped_callback_correlates_by_db_job_id_not_rq_id():
    """A resumed summarize runs under a fresh RQ id, so args[0] is the only reliable correlation."""
    from app.worker.finalizers import on_job_stopped

    _doc_id, job_id = _running_job("summarize")
    on_job_stopped(_FakeRQJob(job_id, rq_id="a-completely-different-rq-id"), None)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "cancelled"


def test_failure_callback_interrupts_an_abandoned_job():
    """A horse that died without reporting: RQ's abandoned-job cleanup calls this."""
    from app.worker.finalizers import on_job_failed

    doc_id, job_id = _running_job("segment")
    on_job_failed(_FakeRQJob(job_id), None, RuntimeError, RuntimeError("horse died"), None)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "interrupted"
        assert session.get(Document, doc_id).status == "interrupted"


def test_failure_callback_leaves_a_background_dedup_document_alone():
    """Mirrors orphan recovery: dedup runs while the reviewer works, so the document stays
    'reviewing'. Marking it interrupted would report a failed stage nobody was watching."""
    from app.worker.finalizers import on_job_failed

    doc_id, job_id = _running_job("dedup")
    on_job_failed(_FakeRQJob(job_id), None, RuntimeError, RuntimeError("x"), None)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "interrupted"
        assert session.get(Document, doc_id).status == "reviewing"  # untouched


def test_failure_callback_does_not_overwrite_an_already_finalized_job():
    """The in-horse failure path fires for an ordinary exception too, AFTER _run has already
    written 'error'. Overwriting it with 'interrupted' would lose the user-facing message."""
    from app.worker.finalizers import on_job_failed

    _doc_id, job_id = _running_job("segment")
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        job.state, job.error = "error", "Something specific went wrong"
        session.commit()

    on_job_failed(_FakeRQJob(job_id), None, RuntimeError, RuntimeError("x"), None)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "error"
        assert job.error == "Something specific went wrong"


def test_mark_terminal_transitions_once_under_a_race():
    """RQ runs the stopped callback AND handle_job_failure for a single stop, and at the worker
    counts this is heading for, parent workers finalize concurrently. First writer wins."""
    _doc_id, job_id = _running_job("segment")

    with get_sessionmaker()() as session:
        assert jobs.mark_terminal(session, job_id, "cancelled", stage="cancelled") is True
    with get_sessionmaker()() as session:
        assert jobs.mark_terminal(session, job_id, "interrupted") is False

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "cancelled"  # the second call changed nothing


def test_enqueue_registers_both_finalizer_callbacks():
    """The wiring itself: without these on the RQ job, nothing finalizes a killed horse."""
    from app.worker.finalizers import on_job_failed, on_job_stopped

    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.enqueue(session, doc_id, "segment", model="m", prompt_version="1")
        queue = queue_for("segment", session.get(Document, doc_id).user_id)
        rq_job = queue.fetch_job(str(job.id))
    try:
        assert rq_job.stopped_callback is on_job_stopped
        assert rq_job.failure_callback is on_job_failed
    finally:
        queue.empty()  # don't leave a job for a real worker to pick up


def test_the_work_horse_replaces_the_pool_it_inherited_before_any_query(monkeypatch):
    """WHEN a job runs in a work-horse, THE SYSTEM SHALL replace the inherited connection pool
    before issuing any SQL, and SHALL NOT close the parent's connections.

    Found live, and it silently defeated the whole force-stop fix: the parent worker opens a pooled
    connection before forking (`app.worker.__main__._user_ids` enumerates queue lanes), the horse
    inherits that socket, and Force stop SIGKILLs the horse mid-transaction. The parent's stopped
    callback then checks out the same connection and dies on
    "can't change 'autocommit' now: connection in transaction status INTRANS" - which pool_pre_ping
    cannot recover, because psycopg raises ProgrammingError rather than a disconnect error.

    Asserted as ORDER, not just "dispose was called": disposing after the first query would leave the
    inherited connection already used and the bug intact. `close=False` is equally load-bearing - the
    default True would close the PARENT's sockets from the child and break the worker that forked us.
    """
    from sqlalchemy import event

    from app.db import get_engine
    from app.worker.tasks import _run

    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id
        session.commit()

    engine = get_engine()
    timeline: list[str] = []
    close_kwargs: list = []
    real_dispose = engine.dispose

    def spy_dispose(close=True):
        timeline.append("dispose")
        close_kwargs.append(close)
        return real_dispose(close=close)

    def on_sql(*_args, **_kwargs):
        timeline.append("sql")

    monkeypatch.setattr(engine, "dispose", spy_dispose)
    event.listen(engine, "before_cursor_execute", on_sql)
    try:
        _run(job_id, lambda session, job, report: report("segmenting", 1, 1))
    finally:
        event.remove(engine, "before_cursor_execute", on_sql)

    assert timeline, "neither a dispose nor a query was observed"
    assert timeline[0] == "dispose", f"queried before replacing the inherited pool: {timeline[:3]}"
    assert close_kwargs == [False], "close must be False or the child closes the parent's sockets"


# The finalizers' defensive branches. These are not padding: D5 was a DB error inside on_job_stopped,
# and it reached the reviewer as "nothing happened" precisely because the except swallowed it. What the
# callback must never do is raise, because RQ re-raises out of the parent worker's monitoring loop -
# that would turn one failed finalization into a worker that stops monitoring every other job.
class _UncorrelatableRQJob:
    """An RQ job whose first argument is not a DB job id - the correlation the finalizers depend on."""

    def __init__(self, args):
        self.args = args
        self.id = "rq-uncorrelatable"


@pytest.mark.parametrize("args", [[], ["not-a-number"], [None]])
def test_finalizers_ignore_a_job_they_cannot_correlate(args):
    """WHEN an RQ job carries no usable DB job id, THE SYSTEM SHALL log and return, NOT raise."""
    from app.worker.finalizers import on_job_failed, on_job_stopped

    on_job_stopped(_UncorrelatableRQJob(args), None)
    on_job_failed(_UncorrelatableRQJob(args), None, RuntimeError, RuntimeError("x"), None)


def test_stopped_callback_ignores_a_job_row_that_no_longer_exists():
    """A document deleted between the kill and the callback must not take the worker down with it."""
    from app.worker.finalizers import on_job_stopped

    on_job_stopped(_FakeRQJob(2_000_000_001), None)


def test_a_callback_that_cannot_reach_the_database_does_not_raise(monkeypatch):
    """WHEN finalizing raises, THE SYSTEM SHALL swallow and log it rather than propagate into RQ.

    This is the D5 shape exactly: the callback ran, could not reach the database, and the run looked
    to the reviewer as though the stop had done nothing. Swallowing is still correct - boot orphan
    recovery is the backstop - but it must be swallowing, not crashing the parent worker.
    """
    from app.worker import finalizers

    def boom():
        raise RuntimeError("connection in transaction status INTRANS")

    monkeypatch.setattr(finalizers, "get_sessionmaker", lambda: boom)

    doc_id, job_id = _running_job("segment")
    finalizers.on_job_stopped(_FakeRQJob(job_id), None)
    finalizers.on_job_failed(_FakeRQJob(job_id), None, RuntimeError, RuntimeError("x"), None)

    # Nothing was written, and nothing escaped.
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "running"


def test_mark_terminal_is_a_no_op_for_a_job_that_does_not_exist():
    """Boot recovery and the callbacks can both name a job that has since been deleted."""
    with get_sessionmaker()() as session:
        assert jobs.mark_terminal(session, 2_000_000_002, "cancelled") is False


def test_a_failed_dispatch_marks_the_job_interrupted_instead_of_leaving_it_queued(monkeypatch):
    """WHEN the RQ dispatch fails, THE SYSTEM SHALL mark the job and document interrupted, and re-raise.

    A job left `queued` with nothing enqueued is the worst outcome: the one-active-job index blocks
    every retry, so the document is wedged by a job no worker will ever pick up.
    """
    from app.services import jobs as jobs_mod

    def boom(*_a, **_kw):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(jobs_mod, "queue_for", boom, raising=False)
    monkeypatch.setattr("app.worker.queues.queue_for", boom)

    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        with pytest.raises(RuntimeError):
            jobs.enqueue(session, doc_id, "segment", model="m", prompt_version="1")

    with get_sessionmaker()() as session:
        job = session.scalars(
            select(Job).where(Job.document_id == doc_id).order_by(Job.id.desc())
        ).first()
        assert job.state == "interrupted"
        assert job.finished_at is not None
        assert session.get(Document, doc_id).status == "interrupted"


def test_a_summarize_job_pins_all_three_models_at_creation():
    """WHEN a summarize job is created on the Gemini path with default settings, THE SYSTEM SHALL
    store the body, title and audit models on the job - so a config change mid-run cannot split one
    delivered document across two models."""
    doc_id = _make_user_and_doc()
    settings = get_settings()
    with get_sessionmaker()() as session:
        job = jobs.create_job(
            session,
            doc_id,
            "summarize",
            model=settings.model_for("body"),
            prompt_version="3",
        )
        assert job.model == "gemini-3.5-flash"
        assert job.title_model == "gemini-2.5-flash"
        assert job.audit_model == "gemini-2.5-flash"


def test_a_non_summarize_job_pins_no_title_or_audit_model():
    """WHEN a job of any other kind is created, THE SYSTEM SHALL leave title_model and audit_model
    NULL - segmentation and dedup make no title or audit call, so a value there would be fiction."""
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(
            session, doc_id, "segment", model="gemini-2.5-flash", prompt_version="3"
        )
        assert job.title_model is None
        assert job.audit_model is None


def test_job_creation_records_a_prompt_fingerprint():
    """WHEN a job is created, THE SYSTEM SHALL stamp the fingerprint of the prompt set in play, so
    provenance no longer depends on a hand-bumped constant."""
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(
            session, doc_id, "segment", model="gemini-2.5-flash", prompt_version="3"
        )
        assert job.prompt_fingerprint
        assert len(job.prompt_fingerprint) == 12


def test_a_provenance_failure_never_blocks_a_job(monkeypatch):
    """WHEN the prompt set cannot be resolved, THE SYSTEM SHALL still create the job and leave the
    fingerprint NULL. Provenance is a record, not a gate - a job that refuses to start because its
    stamp failed is strictly worse than a job carrying no stamp.

    Breaks the hashing INSIDE job_prompt_fingerprint rather than replacing the whole function,
    because the fail-safe under test is that function's own try/except; swapping the function out
    would only prove the monkeypatch works."""
    doc_id = _make_user_and_doc()

    def boom(*_a, **_k):
        raise RuntimeError("prompt resolution exploded")

    monkeypatch.setattr("app.services.provenance.fingerprint", boom)
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="3")
        assert job.id is not None
        assert job.prompt_fingerprint is None


def test_summarize_document_flags_a_fallback_body_for_manual_check(monkeypatch):
    """A row answered by the fallback model was produced by a LESSER model than the job asked for, so
    the reviewer gets the same chip as a truncated body rather than only a line in the worker log."""
    import app.services.summarize_engine as se

    monkeypatch.setattr(
        se,
        "summarize_row",
        lambda pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw: {
            "summaryTitle": "T (Pages 1-1)",
            "summaryDate": "-",
            "summaryText": "body written by the fallback",
            "manualCheck": "",
            "truncated": False,
            "sourceText": "x",
            "model": "gemini-3.5-flash",
            "bodyFallbackFrom": "gemini-2.5-pro",
        },
    )
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        session.add(
            ReviewRow(
                document_id=doc_id,
                idx=0,
                start=1,
                end=1,
                category="1",
                title="A",
                date="-",
                injury_date="-",
                flag="-",
                include=True,
            )
        )
        session.commit()
        job_id = jobs.create_job(
            session, doc_id, "summarize", model="gemini-2.5-pro", prompt_version="1"
        ).id

    summarize_document(job_id)
    with get_sessionmaker()() as session:
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1
        assert summaries[0].manual_check is True
        # The row records what ANSWERED while the job records what was asked for; the pair is the
        # after-the-fact surface for a downgrade, with no schema change.
        assert summaries[0].model == "gemini-3.5-flash"
        assert session.get(Job, job_id).model == "gemini-2.5-pro"


def _seg_row(start, category):
    return {
        "start": start,
        "end": start,
        "category": category,
        "title": "A",
        "date": "-",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
    }


def test_a_missing_ocr_binary_fails_the_segment_job(monkeypatch):
    """WHEN population fails because Tesseract is MISSING, THE SYSTEM SHALL fail the segment job.

    The population call is deliberately best-effort - "a failure here must not fail the job - every
    reader falls back to extracting on demand". That holds for a transient failure and is exactly wrong
    for a missing binary: NO reader can fall back, because nothing can extract. Left swallowed, the
    document segments with no text at all and the operator meets the problem downstream as a Vertex 400
    naming nothing about OCR. `_run` already turns this exception into a friendly "OCR" job error - the
    wrapper was the only thing standing between the two.
    """
    import app.services.page_text as page_text_mod
    import app.services.segment_engine as se

    def missing_binary(session, document_id, pdf_path, total_pages, workers=None):
        raise OcrUnavailableError("no tesseract on this host")

    monkeypatch.setattr(page_text_mod, "populate_document", missing_binary)
    # Whether segmentation was REACHED is the precise signal. Asserting on the error message alone
    # cannot discriminate: the test document's stored_path does not exist, so a later real OCR attempt
    # raises OcrUnavailableError from Poppler too, and the job would show an "OCR" error either way.
    ran = []
    monkeypatch.setattr(
        se,
        "run_segmentation",
        lambda pdf_path, total_pages, progress=None, page_text_fn=None: (
            ran.append(True),
            [_seg_row(1, "1")],
        )[1],
    )
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id

    segment_document(job_id)
    assert ran == [], "a config failure must stop the job BEFORE segmentation, not merely log"
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "error"
        assert "OCR" in job.error  # friendly, and it names the real subsystem
        assert session.get(Document, doc_id).status == "error"


def test_any_other_population_failure_stays_best_effort(monkeypatch):
    """The other half of the pair: a TRANSIENT population failure must still let the job finish, because
    every reader really can fall back to extracting on demand. Pinned so narrowing the catch cannot
    quietly become removing it."""
    import app.services.page_text as page_text_mod
    import app.services.segment_engine as se

    def transient(session, document_id, pdf_path, total_pages, workers=None):
        raise RuntimeError("one page timed out")

    monkeypatch.setattr(page_text_mod, "populate_document", transient)
    monkeypatch.setattr(
        se,
        "run_segmentation",
        lambda pdf_path, total_pages, progress=None, page_text_fn=None: [_seg_row(1, "1")],
    )
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id

    segment_document(job_id)
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        assert session.get(Document, doc_id).status == "reviewing"


# --- C9: an unreadable row is DELIVERED with a notice, and still asks the reviewer to look ---------


def _notice_output(row, pages) -> dict:
    """summarize_row's output for a row where NOTHING could be read: the body IS the notice, and no
    model is credited with writing it."""
    import app.services.summarize_engine as se

    return {
        "summaryTitle": f"T{row['start']} (Pages {row['start']}-{row['end']})",
        "summaryDate": "-",
        "summaryText": se.unreadable_notice(pages),
        "manualCheck": "",
        "sourceText": None,
        "model": None,
        "unreadablePages": list(pages),
        "noticeOnly": True,
    }


def _partial_output(row, pages) -> dict:
    """summarize_row's output for a row summarized off its READABLE pages, notice appended."""
    import app.services.summarize_engine as se

    out = _ok_output(row)
    out.update(
        {
            "summaryText": f"body{row['start']} {se.partial_unreadable_notice(pages)}",
            "model": "gemini-2.5-pro",
            "unreadablePages": list(pages),
            "noticeOnly": False,
        }
    )
    return out


def _stale_notice(doc_id, job_id, **over) -> Summary:
    """A notice Summary left by a previous run, as the reconciliation would find it."""
    fields = {
        "document_id": doc_id,
        "job_id": job_id,
        "idx": 0,
        "title": "OLD",
        "date": "-",
        "text": "a stale notice",
        "unreadable": True,
        "model": None,
        "row_start": 1,
        "row_end": 1,
        "row_category": "1",
    }
    fields.update(over)
    return Summary(**fields)


def test_a_notice_row_is_delivered_and_the_job_still_needs_attention(monkeypatch):
    """Two signals for two audiences: the notice tells the READER what happened, the banner tells the
    REVIEWER to re-run text recognition or exclude the row before delivering. Dropping the banner
    would remove the only thing today that says a page was lost."""
    import app.services.summarize_engine as se

    monkeypatch.setattr(
        se,
        "summarize_row",
        lambda pdf_path, row, *a, **k: _notice_output(row, [int(row["start"])]),
    )

    doc_id, job_id = _doc_with_summarize_rows(2)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "needs_attention"
        summaries = session.scalars(
            select(Summary).where(Summary.document_id == doc_id).order_by(Summary.idx)
        ).all()
        assert len(summaries) == 2  # both rows DELIVERED - neither vanished from the report
        assert all(s.unreadable is True for s in summaries)
        assert all(s.model is None for s in summaries)  # no model wrote these bodies
        assert "unintelligible" in summaries[0].text.lower()
        # Pool completion order is not fixed, so compare as a set.
        assert sorted(r["pages"] for r in job.attention["rows"]) == ["1-1", "2-2"]
        assert "could not read page" in job.attention["rows"][0]["reason"]


def test_a_partially_unreadable_row_is_flagged_but_the_job_completes(monkeypatch):
    """It carries a real summary AND the appended notice, so there is nothing for the reviewer to
    recover: the flag is for querying later, not for prompting now."""
    import app.services.summarize_engine as se

    monkeypatch.setattr(
        se, "summarize_row", lambda pdf_path, row, *a, **k: _partial_output(row, [1])
    )

    doc_id, job_id = _doc_with_summarize_rows(1)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "done"
        assert job.progress()["attention"] is None  # no banner: nothing to act on
        summary = session.scalars(select(Summary).where(Summary.document_id == doc_id)).one()
        assert summary.unreadable is True
        assert summary.model == "gemini-2.5-pro"  # a model DID write this body
        assert "not covered by this summary" in summary.text


def test_summarize_again_re_reads_a_notice_row_instead_of_reusing_it(monkeypatch):
    """The banner tells the reviewer to "summarize again". Skip-done reuse would make that a no-op,
    and a transient Tesseract timeout would become permanent by having been DELIVERED - the failure
    page_text.py guards against in four separate places."""
    import app.services.summarize_engine as se

    calls: list[int] = []

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        calls.append(int(row["start"]))
        return _ok_output(row)

    monkeypatch.setattr(se, "summarize_row", fake)

    doc_id, job_id = _doc_with_summarize_rows(1)
    with get_sessionmaker()() as session:
        session.add(_stale_notice(doc_id, job_id))
        session.commit()

    summarize_document(job_id)

    assert calls == [1]  # re-read, NOT skipped as already done
    with get_sessionmaker()() as session:
        summary = session.scalars(select(Summary).where(Summary.document_id == doc_id)).one()
        assert summary.unreadable is False  # the retry recovered the page
        assert summary.text == "body1"


def test_a_reviewer_edited_notice_row_is_left_alone(monkeypatch):
    """Having corrected the entry by hand, the reviewer does not want it replaced by a fresh notice."""
    import app.services.summarize_engine as se

    calls: list[int] = []

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        calls.append(int(row["start"]))
        return _ok_output(row)

    monkeypatch.setattr(se, "summarize_row", fake)

    doc_id, job_id = _doc_with_summarize_rows(1)
    with get_sessionmaker()() as session:
        session.add(_stale_notice(doc_id, job_id, edited_text="reviewer wording"))
        session.commit()

    summarize_document(job_id)

    assert calls == []  # reused, so the hand-written correction survives
    with get_sessionmaker()() as session:
        summary = session.scalars(select(Summary).where(Summary.document_id == doc_id)).one()
        assert summary.edited_text == "reviewer wording"


def test_a_partially_unreadable_summary_is_reused_like_any_other(monkeypatch):
    """It holds real clinical content, so discarding it to re-OCR on every run would be a regression
    rather than a retry. Only a notice-ONLY row (model NULL) is re-read."""
    import app.services.summarize_engine as se

    calls: list[int] = []

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        calls.append(int(row["start"]))
        return _ok_output(row)

    monkeypatch.setattr(se, "summarize_row", fake)

    doc_id, job_id = _doc_with_summarize_rows(1)
    with get_sessionmaker()() as session:
        session.add(_stale_notice(doc_id, job_id, model="gemini-2.5-pro", text="real body"))
        session.commit()

    summarize_document(job_id)

    assert calls == []
    with get_sessionmaker()() as session:
        summary = session.scalars(select(Summary).where(Summary.document_id == doc_id)).one()
        assert summary.text == "real body"  # the clinical content was not thrown away


def test_a_notice_row_is_not_counted_as_proof_the_model_answers(monkeypatch):
    """`generated` gates the give-up guard, and a notice involves no model call at all. Counting it
    would break `generated == 0` on a document whose every real row is being refused, so the job
    would pause and auto-resume into the same refusal - the 96-minute grind the guard prevents."""
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        if int(row["start"]) == 1:
            return _notice_output(row, [1])  # delivered, but no model answered for it
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _NoopQueue())
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 99)
    monkeypatch.setattr(get_settings(), "summarize_giveup_after_failures", 3)

    doc_id, job_id = _doc_with_summarize_rows(8)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        # needs_attention (gave up), NOT paused: the notice was not a model success.
        assert job.state == "needs_attention"
        assert "AI service" in (job.error or "")
        # The notice row is still kept - giving up never discards delivered work.
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert [s.row_start for s in summaries] == [1]


def test_the_worker_seeds_each_row_with_the_pages_that_failed_extraction(monkeypatch):
    """summarize_engine is DB-free, so the pages `page_texts` records as failed reach it as row data.
    One query for the whole document, sliced to each row's own range."""
    import app.services.summarize_engine as se

    from app.models import PageText

    seen: dict[int, list] = {}

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        seen[int(row["start"])] = row.get("unreadable_pages")
        return _ok_output(row)

    monkeypatch.setattr(se, "summarize_row", fake)

    doc_id, job_id = _doc_with_summarize_rows(3)  # rows 1-1, 2-2, 3-3
    with get_sessionmaker()() as session:
        session.add(PageText(document_id=doc_id, page=2, text="", extract_ok=False, char_count=0))
        session.add(
            PageText(document_id=doc_id, page=3, text="fine", extract_ok=True, char_count=4)
        )
        session.commit()

    summarize_document(job_id)

    assert seen[1] == []  # nothing failed inside this row's range
    assert seen[2] == [2]  # the failed page, sliced to the row that owns it
    assert seen[3] == []  # stored successfully, so never reported as unreadable


def test_pipeline_workers_agrees_between_config_and_compose():
    """WHEN the summarize row concurrency is changed, THE SYSTEM SHALL change it in both places.

    `docker-compose.yml` passes PIPELINE_WORKERS explicitly, so a container reads the COMPOSE default
    and never the one in `config.py`. Editing config alone changes nothing on a deployed box.
    `DUPE_SIMILARITY_OVERRIDE` is that exact bug in this tree's history - config said 0.99, compose
    said 0.90, and production ran 0.90 from #81 until the key was finally added to the box `.env` on
    2026-08-25. The same guard already exists for PAGE_TEXT_WORKERS; this adds it for the setting that
    governs summarize throughput.
    """
    import re
    from pathlib import Path

    from app.config import get_settings

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    match = re.search(
        r"PIPELINE_WORKERS:\s*\$\{PIPELINE_WORKERS:-(\d+)\}", compose.read_text(encoding="utf-8")
    )
    assert match, "docker-compose.yml no longer passes PIPELINE_WORKERS"
    assert int(match.group(1)) == get_settings().pipeline_workers, (
        "docker-compose.yml and config.py disagree on PIPELINE_WORKERS, so a deployed container "
        "would use the compose value and ignore the code default"
    )


# ---------------------------------------------------------------------------------------------
# The end-versus-pause decision must not depend on completion order.
#
# Every row is submitted up front and results arrive through `drain_pool`, which is `as_completed`.
# The give-up guard is `generated == 0 and transient_failures >= giveup_after_failures`, so before
# this fix, hitting the threshold cancelled the queued rows and broke immediately - throwing away the
# results of rows that were already RUNNING, which `cancel()` cannot stop. Whether the job ENDED or
# PAUSED therefore turned on whether the failures happened to complete before a success.
#
# Measured on 2026-08-25. Against `origin/main`, with the lane count varied explicitly, the
# end-versus-pause test below fails on 8 of 8 runs at 8 lanes and 6 of 8 at 5 lanes, and passes at 1
# and 2. Separately, raising the config default to 5 made the two pre-existing give-up tests fail on
# 3 of 6 runs. So the bug is LATENT at the shipped concurrency of 2 - which is why nothing had caught
# it - and becomes probable as soon as the lane count is raised, i.e. exactly when the summarize
# throughput lever recorded in `config.py` is taken.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("lanes", [1, 2, 5, 8])
def test_one_success_keeps_the_job_paused_at_any_concurrency(monkeypatch, lanes):
    """WHILE at least one row has succeeded, THE SYSTEM SHALL pause rather than end - whatever the
    lane count, and whatever order the results land in."""
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        if int(row["start"]) == 1:
            return _ok_output(row)
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    class _FakeQueue:
        def enqueue_in(self, td, fn, arg, job_timeout=None, on_stopped=None, on_failure=None):
            return type("_J", (), {"id": "rq-resume-1"})()

    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _FakeQueue())
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 99)
    monkeypatch.setattr(get_settings(), "summarize_giveup_after_failures", 3)
    monkeypatch.setattr(get_settings(), "pipeline_workers", lanes)

    doc_id, job_id = _doc_with_summarize_rows(12)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "paused", (
            f"at {lanes} lanes the job ended instead of pausing, even though a row succeeded"
        )
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1 and summaries[0].row_start == 1


@pytest.mark.parametrize("lanes", [1, 2, 5, 8])
def test_a_model_refusing_everything_still_ends_the_job(monkeypatch, lanes):
    """The other direction, so the fix does not simply make give-up unreachable: with NO row
    succeeding, the job must still END rather than pause into a resume loop."""
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    class _FakeQueue:
        def enqueue_in(self, td, fn, arg, job_timeout=None, on_stopped=None, on_failure=None):
            return type("_J", (), {"id": "rq-resume-1"})()

    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _FakeQueue())
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 99)
    monkeypatch.setattr(get_settings(), "summarize_giveup_after_failures", 3)
    monkeypatch.setattr(get_settings(), "pipeline_workers", lanes)

    _doc_id, job_id = _doc_with_summarize_rows(12)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "needs_attention"


@pytest.mark.parametrize("lanes", [1, 5])
def test_giving_up_does_not_report_the_skipped_rows_as_failures(monkeypatch, lanes):
    """The rows we cancel are SKIPPED, not broken. `as_completed` yields cancelled futures and
    `.result()` on one raises CancelledError, so without an explicit guard every skipped row would be
    classified as a permanent failure and reported to the reviewer as a document that could not be
    summarized.

    Asserts the reported REASONS rather than the row COUNT. The count is not a property the executor
    guarantees: every row is submitted up front, so how many have already run by the time the
    threshold trips is timing. This mock refuses instantly, so at one lane the single worker can drain
    all thirty work items before the main thread observes the third failure, and `cancel()` then
    cancels nothing. Two earlier versions of this test asserted a count - `< 10` at any lane count
    (failed about 1 run in 18 under load) and then an exact 3 at one lane (failed every run) - which
    is the same class of timing-dependent assertion this whole fix exists to remove.

    What IS guaranteed: a row that never ran is never reported as a failure. So every reported reason
    must be the real refusal, never a CancelledError leaking through `.result()`.
    """
    import app.services.summarize_engine as se
    from google.genai import errors

    from app.worker import tasks as tasks_mod

    def fake(pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    class _FakeQueue:
        def enqueue_in(self, td, fn, arg, job_timeout=None, on_stopped=None, on_failure=None):
            return type("_J", (), {"id": "rq-resume-1"})()

    monkeypatch.setattr(se, "summarize_row", fake)
    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind, user_id=None: _FakeQueue())
    monkeypatch.setattr(get_settings(), "summarize_pause_after", 99)
    monkeypatch.setattr(get_settings(), "summarize_giveup_after_failures", 3)
    monkeypatch.setattr(get_settings(), "pipeline_workers", lanes)

    _doc_id, job_id = _doc_with_summarize_rows(30)
    summarize_document(job_id)

    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "needs_attention"
        rows = (job.attention or {}).get("rows") or []
        assert rows, "no rows reported at all - real refusals must still reach the reviewer"
        leaked = [r for r in rows if "busy" not in (r.get("reason") or "")]
        assert not leaked, (
            f"{len(leaked)} of {len(rows)} reported rows carry a reason that is not the model's "
            f"refusal - a skipped row is being reported as a failure: {leaked[:2]}"
        )
