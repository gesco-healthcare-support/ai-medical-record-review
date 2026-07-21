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
from app.config import get_settings
from app.db import get_sessionmaker
from app.errors import EmptyExtractionError, OcrUnavailableError
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


def test_summarize_document_preserves_row_order_under_parallelism(monkeypatch):
    """Rows summarize on a thread pool; an inverse-sleep mock finishes them out of order, yet the
    persisted Summary set must stay in idx (document) order."""
    import time

    import app.services.summarize_engine as se

    def fake_summarize(pdf_path, row, model=None, prompt=None):
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


def test_summarize_persists_per_row_and_reuses_done_on_rerun(monkeypatch):
    """Skip-done by row identity: an existing summary is reused (its edit preserved), only the
    missing row is generated, and it is positioned to the current row order."""
    import app.services.summarize_engine as se

    calls: list[int] = []

    def fake(pdf_path, row, model=None, prompt=None):
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

    def fake(pdf_path, row, model=None, prompt=None):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "rate limited, retry"}})

    monkeypatch.setattr(se, "summarize_row", fake)

    scheduled: dict = {}

    class _FakeQueue:
        def enqueue_in(self, td, fn, arg, job_timeout=None):
            scheduled["delay"] = td.total_seconds()
            scheduled["arg"] = arg
            return type("_J", (), {"id": "rq-resume-1"})()

    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind: _FakeQueue())
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


def test_summarize_needs_attention_on_permanent_keeps_partial(monkeypatch):
    """A permanent per-row failure (empty OCR) ends the job 'needs_attention' naming the row,
    while every readable row is still persisted."""
    import app.services.summarize_engine as se

    def fake(pdf_path, row, model=None, prompt=None):
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

    monkeypatch.setattr(
        classification,
        "classify",
        lambda title, page_text=None: Classification("3", "high", "rules", needs_review=False),
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
        assert [r.category for r in rows] == ["3", "3"]  # per-row classification applied
