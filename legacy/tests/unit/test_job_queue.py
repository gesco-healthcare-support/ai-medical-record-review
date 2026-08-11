"""T4: DB-backed job queue - concurrency, per-document exclusivity, orphan sweep.

Targets are stubs coordinated with threading.Events; no engine or network is involved.
"""

import threading
import time

from mrr_ai.services import job_queue


def _get_job_state(app, job_id):
    from mrr_ai.extensions import db
    from mrr_ai.models import Job

    with app.app_context():
        job = db.session.get(Job, job_id)
        return job.state, job.error


def _wait_for_state(app, job_id, wanted, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state, _ = _get_job_state(app, job_id)
        if state == wanted:
            return True
        time.sleep(0.02)
    return False


def _document_status(app, document_id):
    from mrr_ai.extensions import db
    from mrr_ai.models import Document

    with app.app_context():
        return db.session.get(Document, document_id).status


def test_two_documents_run_concurrently(app, make_document):
    doc_a, doc_b = make_document(), make_document()
    both_started = threading.Barrier(3, timeout=10)
    release = threading.Event()

    def target(report):
        both_started.wait()
        release.wait(timeout=10)

    with app.app_context():
        job_a = job_queue.submit(doc_a, "segment", target, model="m", prompt_version="2")
        job_b = job_queue.submit(doc_b, "summarize", target, model="m", prompt_version="2")
        job_a_id, job_b_id = job_a.id, job_b.id

    both_started.wait()  # both targets on worker threads at the same time
    assert _document_status(app, doc_a) == "segmenting"
    assert _document_status(app, doc_b) == "summarizing"
    release.set()

    assert _wait_for_state(app, job_a_id, "done")
    assert _wait_for_state(app, job_b_id, "done")
    assert _document_status(app, doc_a) == "reviewing"  # segment -> ready for review
    assert _document_status(app, doc_b) == "done"


def test_second_job_on_same_document_rejected(app, make_document):
    doc = make_document()
    release = threading.Event()

    def target(report):
        release.wait(timeout=10)

    with app.app_context():
        first = job_queue.submit(doc, "segment", target, model="m", prompt_version="2")
        second = job_queue.submit(doc, "segment", target, model="m", prompt_version="2")
        first_id = first.id

    assert second is None
    release.set()
    assert _wait_for_state(app, first_id, "done")

    with app.app_context():
        third = job_queue.submit(doc, "summarize", target, model="m", prompt_version="2")
        assert third is not None
    release.set()


def test_unexpected_target_exception_shows_generic_message(app, make_document):
    from mrr_ai.errors import GENERIC_USER_MESSAGE

    doc = make_document()

    def target(report):
        # A raw technical error (e.g. a vendor 400) must not reach the user verbatim.
        raise RuntimeError("window 3 failed after retries")

    with app.app_context():
        job = job_queue.submit(doc, "segment", target, model="m", prompt_version="2")
        job_id = job.id

    assert _wait_for_state(app, job_id, "error")
    _, error = _get_job_state(app, job_id)
    assert error == GENERIC_USER_MESSAGE
    assert "window 3 failed" not in error  # technical detail is logged, not shown
    assert _document_status(app, doc) == "error"


def test_pipeline_error_shows_its_user_message(app, make_document):
    from mrr_ai.errors import OcrUnavailableError

    doc = make_document()

    def target(report):
        raise OcrUnavailableError("tesseract not on PATH")

    with app.app_context():
        job = job_queue.submit(doc, "summarize", target, model="m", prompt_version="2")
        job_id = job.id

    assert _wait_for_state(app, job_id, "error")
    _, error = _get_job_state(app, job_id)
    assert error == OcrUnavailableError.user_message
    assert _document_status(app, doc) == "error"


def test_orphan_sweep_marks_interrupted(app, make_document):
    doc = make_document()
    from mrr_ai.extensions import db
    from mrr_ai.models import Job

    with app.app_context():
        db.session.add(
            Job(document_id=doc, kind="segment", state="running", model="m", prompt_version="2")
        )
        db.session.commit()
        swept = job_queue.sweep_orphans()

    assert swept == 1
    with app.app_context():
        job = Job.query.filter_by(document_id=doc).one()
        assert job.state == "interrupted"
        assert job.finished_at is not None
    assert _document_status(app, doc) == "interrupted"


def test_progress_writes_throttled_but_stage_changes_flush(app, make_document):
    doc = make_document()

    def target(report):
        report("categorizing", 0, 200)
        for i in range(1, 200):  # same-stage ticks: rate-limited to ~1/second
            report("categorizing", i, 200)
        report("verifying", 0, 50)  # stage change: must write immediately

    with app.app_context():
        job = job_queue.submit(doc, "segment", target, model="m", prompt_version="2")
        job_id = job.id

    assert _wait_for_state(app, job_id, "done")
    from mrr_ai.extensions import db
    from mrr_ai.models import Job

    with app.app_context():
        job = db.session.get(Job, job_id)
        assert job.stage == "verifying"  # the stage flip was written
        # 199 same-stage ticks in well under a second: nearly all skipped.
        assert job.total == 50
