"""P4a: the job service (one-active-job invariant + enqueue routing) and the worker state machine.

The invariant + state-machine tests run against docker Postgres (the partial-unique index is a
real DB constraint); the pipeline itself (run_segmentation / summarize_row) is MOCKED so no torch
or Vertex call is needed. Test users use the pytest-auth- prefix so conftest cleans them + their
jobs/rows.
"""

import uuid

import pytest
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.db import get_sessionmaker
from app.errors import OcrUnavailableError
from app.models import Document, Job, ReviewRow, SegmentRow, Summary, User
from app.services import jobs
from app.worker.queues import queue_for, worker_fn
from app.worker.tasks import _run, segment_document, summarize_document
from tests.conftest import unique_test_email


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
    assert queue_for("segment").name == "segment"
    assert queue_for("summarize").name == "summarize"
    assert worker_fn("segment").endswith("tasks.segment_document")
    assert worker_fn("summarize").endswith("tasks.summarize_document")


def test_create_job_sets_queued_and_document_status():
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1")
        assert job.state == "queued"
        assert session.get(Document, doc_id).status == "segmenting"


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
    import app.services.segment_engine as se

    monkeypatch.setattr(
        se,
        "run_segmentation",
        lambda pdf_path, total_pages, progress=None: [
            {
                "start": 1,
                "end": 1,
                "category": "1",
                "title": "A",
                "date": "-",
                "injury_date": "-",
                "flag": "-",
                "suggest_merge": False,
            }
        ],
    )
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id

    segment_document(job_id)
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        review = session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        segment = session.scalars(select(SegmentRow).where(SegmentRow.job_id == job_id)).all()
        assert len(review) == 1 and review[0].category == "1"
        assert len(segment) == 1


def test_summarize_document_persists_summaries(monkeypatch):
    import app.services.summarize_engine as se

    monkeypatch.setattr(
        se,
        "summarize_row",
        lambda pdf_path, row, model=None, prompt=None: {
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
        assert session.get(Job, job_id).state == "done"
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1 and summaries[0].title == "T (Pages 1-1)"


def test_enqueue_dispatches_to_the_right_queue():
    doc_id = _make_user_and_doc()
    queue = queue_for("segment")
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
