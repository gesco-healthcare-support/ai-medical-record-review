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
from app.worker.tasks import _run, dedup_document, segment_document, summarize_document
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
        lambda pdf_path, total_pages, progress=None: [_row(1, "1"), _row(2, "9")],
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
        assert [r.category for r in review] == ["1", "9"]
        assert len(segment) == 2
        # include follows the category summarize_default: cat 1 on, Depositions (9) off.
        assert review[0].include is True
        assert review[1].include is False


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
        lambda pdf_path, row, model=None, prompt=None: {
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


def test_job_progress_exposes_attention_rows(monkeypatch):
    """H1: the status payload (Job.progress) surfaces the per-row failure detail so the UI can list
    + highlight exactly which sub-documents failed, with the reason. (Worker persistence itself is
    covered by test_summarize_needs_attention_on_permanent_keeps_partial.)"""
    import app.services.summarize_engine as se

    def fake(pdf_path, row, model=None, prompt=None):
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
        se, "summarize_row", lambda pdf_path, row, model=None, prompt=None: time.sleep(1.5)
    )

    scheduled: dict = {}

    class _FakeQueue:
        def enqueue_in(self, td, fn, arg, job_timeout=None):
            scheduled["arg"] = arg
            return type("_J", (), {"id": "rq-resume-timeout"})()

    monkeypatch.setattr(tasks_mod, "queue_for", lambda kind: _FakeQueue())
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
        lambda title, page_text=None: Classification("9", "high", "rules", needs_review=False),
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
        assert [r.category for r in rows] == ["9", "9"]  # per-row classification applied
        # include re-derived from the new category: Depositions (9) is off-by-default.
        assert all(r.include is False for r in rows)


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
    monkeypatch.setattr(
        "app.services.ocr.extract_text_from_selected_pages",
        lambda path, pages, mark_pages=False: texts[pages[0]],
    )
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


def test_dedup_document_skips_ocred_rows_survives_failure_and_rejects(monkeypatch):
    """dedup_document reuses stored source_text (no re-OCR), tolerates a per-row OCR failure, and
    assigns NO group when the confirm step rejects a candidate cluster."""
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

    def fake_ocr(path, pages, mark_pages=False):
        ocr_calls.append(pages[0])
        if pages[0] == 2:
            raise RuntimeError("ocr boom")  # row1: per-row OCR failure is tolerated
        return "alpha beta gamma delta epsilon"  # row2: same text as row0 -> candidate cluster

    monkeypatch.setattr("app.services.ocr.extract_text_from_selected_pages", fake_ocr)
    # Confirm rejects the candidate cluster -> no group is assigned.
    monkeypatch.setattr("app.services.dedup.confirm_cluster", lambda members, model=None: [])

    dedup_document(job_id)

    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        rows = {
            r.idx: r
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        }
    assert 1 not in ocr_calls  # row0 already had source_text -> its page was not re-OCR'd
    assert rows[0].source_text == "alpha beta gamma delta epsilon"  # unchanged
    assert rows[1].source_text == ""  # OCR failed -> empty, run still completed
    assert all(r.dupe_group is None for r in rows.values())  # confirm rejected the cluster


def _dedup_rows(doc_id, specs):
    """Seed ReviewRows from (start, end, include, dismissed, group, text) tuples + a dedup job id."""
    with get_sessionmaker()() as session:
        for idx, (start, end, include, dismissed, group, text) in enumerate(specs):
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


def test_dedup_document_covers_excluded_rows_and_keeps_an_unchanged_dismissal(monkeypatch):
    """Scope is EVERY row - an excluded copy is still clustered (General/Depositions rows are
    excluded by default, and that is where re-scanned letters live) - and a dismissed cluster whose
    copies are unchanged comes back still dismissed, so a settled "not duplicates" stays quiet."""
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
    monkeypatch.setattr(
        "app.services.ocr.extract_text_from_selected_pages",
        lambda path, pages, mark_pages=False: texts[pages[0]],
    )
    monkeypatch.setattr("app.services.dedup.confirm_cluster", lambda members, model=None: members)

    dedup_document(job_id)

    rows = _rows_by_idx(doc_id)
    assert rows[1].include is False and rows[1].dupe_group is not None
    assert rows[0].dupe_group == rows[1].dupe_group  # the excluded copy is clustered with its twin
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


def test_dedup_document_keeps_the_reviewers_kept_copy(monkeypatch):
    """A re-check must not forget a keep-one resolution: dupe_primary survives the run (only
    dupe_group is recomputed), so the cluster comes back resolved rather than needing review."""
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
    assert rows[0].dupe_group == rows[1].dupe_group is not None  # re-clustered
    assert rows[0].dupe_primary is True  # the kept copy is still marked
    assert rows[1].dupe_primary is False


def _dedup_jobs(doc_id):
    with get_sessionmaker()() as session:
        return session.scalars(
            select(Job).where(Job.document_id == doc_id, Job.kind == "dedup")
        ).all()


def test_chain_dedup_skips_when_identify_not_done():
    from app.worker.tasks import _chain_dedup

    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(
            session, doc_id, "segment", model="m", prompt_version="1"
        ).id  # queued
    _chain_dedup(job_id)  # identify did not finish -> no dedup enqueued
    assert _dedup_jobs(doc_id) == []


def test_chain_dedup_swallows_conflict_when_a_job_is_active():
    from app.worker.tasks import _chain_dedup

    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        seg = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1")
        seg.state = "done"
        session.add(
            Job(
                document_id=doc_id, kind="summarize", state="running", model="m", prompt_version="1"
            )
        )
        session.commit()
        seg_id = seg.id
    _chain_dedup(seg_id)  # a job is already active -> JobConflict is swallowed, no raise
    assert _dedup_jobs(doc_id) == []


def test_chain_dedup_swallows_generic_error(monkeypatch):
    from app.worker.tasks import _chain_dedup

    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        seg = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1")
        seg.state = "done"
        session.commit()
        seg_id = seg.id

    def _boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.jobs.enqueue", _boom)
    _chain_dedup(seg_id)  # a non-conflict failure is logged + swallowed (never fails identify)
    assert _dedup_jobs(doc_id) == []
