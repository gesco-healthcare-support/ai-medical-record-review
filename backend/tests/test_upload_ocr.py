"""Reading a record's pages at upload, with no model - and on segment workers while the model is off.

The test box's rented model server is off between pod days. Before this, a record's pages were read
only at the start of Identify, which needs the model: a 5,000-page record spent ~45 minutes reading
pages while the rented server waited. These pin the three pieces: the upload queues the reading,
the job fills the page-text store, and a segment worker does the reading while it waits for the
model server - without ever taking a model job before the server has passed its check.
"""

import logging
import signal
import uuid

from rq import Queue
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Document, PageText, User
from app.services import page_text as pt
from app.worker import queues
from app.worker import tasks
from tests.conftest import unique_test_email


def _doc(pages: int = 3) -> str:
    with get_sessionmaker()() as session:
        user = User(
            email=unique_test_email(),
            name="UploadOcr",
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
            page_count=pages,
        )
        session.add(document)
        session.commit()
        return document.id


def _pages(doc_id: str) -> list[int]:
    with get_sessionmaker()() as session:
        return sorted(session.scalars(select(PageText.page).where(PageText.document_id == doc_id)))


# --- queueing --------------------------------------------------------------------------------------


def test_the_reading_is_queued_untracked_with_a_timeout_scaled_to_the_record():
    """No ``jobs`` row: one active job per document is enforced in the database, so a tracked
    reading job would make the reviewer's Identify answer 409 while it ran."""
    queue = Queue(queues.OCR_QUEUE, connection=queues.get_redis())
    queue.empty()
    try:
        assert queues.enqueue_upload_ocr("doc-1", 5000) is True
        assert queue.count == 1
        job = queue.jobs[0]
        assert job.func_name == "app.worker.tasks.ocr_uploaded_document"
        assert job.args == ("doc-1",)
        assert job.timeout == get_settings().effective_job_timeout(5000)
    finally:
        queue.empty()


def test_a_refused_queue_does_not_fail_the_upload(monkeypatch, caplog):
    def refuse(*_args, **_kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(Queue, "enqueue", refuse)
    with caplog.at_level(logging.WARNING, logger=queues.__name__):
        assert queues.enqueue_upload_ocr("doc-1", 10) is False
    assert any("could not queue the page reading" in r.getMessage() for r in caplog.records)


# --- the job ---------------------------------------------------------------------------------------


def test_the_job_fills_the_page_text_store(monkeypatch):
    monkeypatch.setattr(pt, "_extract", lambda _path, page: (f"page {page}", True))
    doc_id = _doc(pages=4)
    tasks.ocr_uploaded_document(doc_id)
    assert _pages(doc_id) == [1, 2, 3, 4]


def test_the_job_does_nothing_for_a_record_deleted_before_it_ran(monkeypatch):
    def never(_path, _page):
        raise AssertionError("read pages of a record that no longer exists")

    monkeypatch.setattr(pt, "_extract", never)
    tasks.ocr_uploaded_document(str(uuid.uuid4()))


def test_a_failed_reading_is_logged_and_left_for_identify(monkeypatch, caplog):
    """Best effort: Identify reads whatever is still missing and reports an OCR problem itself."""

    def broken(*_args, **_kwargs):
        raise RuntimeError("tesseract crashed")

    monkeypatch.setattr(pt, "populate_document", broken)
    doc_id = _doc(pages=2)
    with caplog.at_level(logging.WARNING, logger=tasks.__name__):
        tasks.ocr_uploaded_document(doc_id)
    assert any("upload page reading failed" in r.getMessage() for r in caplog.records)


# --- segment workers -------------------------------------------------------------------------------


class _FakeWorker:
    built: list = []

    def __init__(self, queues_, connection=None, **kwargs):
        _FakeWorker.built.append([q.name for q in queues_])

    def work(self, **kwargs):
        _FakeWorker.work_kwargs = kwargs


def test_a_segment_worker_reads_the_ocr_queue_but_hands_only_lanes_to_fairness(monkeypatch):
    from app.worker import __main__ as worker_main
    from app.worker import fairness

    monkeypatch.setattr(_FakeWorker, "built", [])
    monkeypatch.setattr(worker_main, "RoundRobinWorker", _FakeWorker)
    monkeypatch.setattr(worker_main, "_user_ids", lambda: [7])
    worker_main.main(["segment"])
    assert _FakeWorker.built[-1] == ["segment", "segment:7", "ocr"]
    assert fairness.served() == ("segment", "segment:7")


def test_a_summarize_worker_does_not_read_the_ocr_queue(monkeypatch):
    from app.worker import __main__ as worker_main

    monkeypatch.setattr(_FakeWorker, "built", [])
    monkeypatch.setattr(worker_main, "RoundRobinWorker", _FakeWorker)
    monkeypatch.setattr(worker_main, "_user_ids", lambda: [7])
    worker_main.main(["summarize"])
    assert "ocr" not in _FakeWorker.built[-1]


def test_a_segment_worker_reads_uploads_while_it_waits_and_a_summarize_worker_just_waits(
    monkeypatch,
):
    from app.worker import __main__ as worker_main

    seen = []
    monkeypatch.setattr(
        "app.services.llm.preflight.wait_for_backends", lambda **kw: seen.append(kw.get("sleep"))
    )
    monkeypatch.setattr(worker_main, "RoundRobinWorker", _FakeWorker)
    monkeypatch.setattr(worker_main, "_user_ids", lambda: [])
    worker_main.main(["segment"])
    worker_main.main(["summarize"])
    assert seen == [worker_main._read_uploads_then_sleep, None]


def test_the_pause_reads_queued_uploads_then_sleeps_only_what_is_left(monkeypatch):
    from app.worker import __main__ as worker_main

    clock = iter([100.0, 112.0])  # the reading took 12 s of a 30 s pause
    monkeypatch.setattr(worker_main.time, "monotonic", lambda: next(clock))
    slept = []
    monkeypatch.setattr(worker_main.time, "sleep", slept.append)
    drained = []
    monkeypatch.setattr(worker_main, "_drain_ocr_queue", lambda: drained.append(1))
    worker_main._read_uploads_then_sleep(30.0)
    assert drained == [1]
    assert slept == [18.0]


def test_a_failed_reading_never_takes_a_waiting_worker_down(monkeypatch, caplog):
    from app.worker import __main__ as worker_main

    def broken():
        raise RuntimeError("redis went away")

    monkeypatch.setattr(worker_main, "_drain_ocr_queue", broken)
    monkeypatch.setattr(worker_main.time, "sleep", lambda _s: None)
    with caplog.at_level(logging.WARNING, logger=worker_main.__name__):
        worker_main._read_uploads_then_sleep(30.0)
    assert any(
        "reading uploaded records while waiting failed" in r.getMessage() for r in caplog.records
    )


def test_an_empty_ocr_queue_starts_no_rq_worker(monkeypatch):
    """A night of 30-second checks must not write RQ start/stop lines every time."""
    from app.worker import __main__ as worker_main

    Queue(queues.OCR_QUEUE, connection=queues.get_redis()).empty()
    monkeypatch.setattr(_FakeWorker, "built", [])
    monkeypatch.setattr(worker_main, "Worker", _FakeWorker)
    worker_main._drain_ocr_queue()
    assert _FakeWorker.built == []


def test_the_drain_reads_only_the_ocr_queue_in_a_burst_and_restores_signal_handlers(monkeypatch):
    """Only the ocr queue: no model job can be taken before the model server has passed. RQ installs
    its own SIGTERM handler for the burst; the worker's own is put back afterwards."""
    from app.worker import __main__ as worker_main

    queue = Queue(queues.OCR_QUEUE, connection=queues.get_redis())
    queue.empty()
    queue.enqueue("app.worker.tasks.ocr_uploaded_document", "doc-1")
    before = signal.getsignal(signal.SIGTERM)

    class _Burst(_FakeWorker):
        def work(self, **kwargs):
            _FakeWorker.work_kwargs = kwargs
            signal.signal(signal.SIGTERM, lambda *_a: None)  # what RQ does during work()

    monkeypatch.setattr(_FakeWorker, "built", [])
    monkeypatch.setattr(worker_main, "Worker", _Burst)
    try:
        worker_main._drain_ocr_queue()
    finally:
        queue.empty()
    assert _FakeWorker.built == [["ocr"]]
    assert _FakeWorker.work_kwargs == {"burst": True, "with_scheduler": False}
    assert signal.getsignal(signal.SIGTERM) == before
