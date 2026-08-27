"""The per-page OCR store: extract once, reuse everywhere, and keep the errored/blank distinction.

The point of the store is that the pipeline used to OCR the same page up to four times per document
and threw all of it away on a re-run. These tests pin the two properties that make it worth having -
a stored page is never re-extracted, and a page that FAILED is distinguishable from one that is
genuinely blank - because losing either silently turns the store into a slower version of what it
replaced.
"""

import uuid

import pytest
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.db import get_sessionmaker
from app.errors import OcrUnavailableError
from app.models import Document, PageText, User
from app.services import ocr
from app.services import page_text as pt
from tests.conftest import unique_test_email


def _doc(pages: int = 3) -> str:
    with get_sessionmaker()() as session:
        user = User(
            email=unique_test_email(),
            name="PageText",
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


def test_population_stores_every_page_and_is_idempotent(monkeypatch):
    """WHEN a document is populated twice, THE SYSTEM SHALL extract each page exactly once.

    Idempotency is what lets the segment job call this unconditionally: a re-segment, a resumed run
    and a manual re-click must all pay for the pages nobody has read yet, and nothing else.
    """
    calls = []
    monkeypatch.setattr(
        pt, "_extract", lambda path, page: (calls.append(page), (f"page {page} text", True))[1]
    )
    doc_id = _doc(pages=4)

    with get_sessionmaker()() as session:
        assert pt.populate_document(session, doc_id, "/x.pdf", 4) == 4
    assert sorted(calls) == [1, 2, 3, 4]

    with get_sessionmaker()() as session:
        assert pt.populate_document(session, doc_id, "/x.pdf", 4) == 0  # nothing left to do
    assert sorted(calls) == [1, 2, 3, 4]  # and nothing re-extracted


def test_a_stored_page_is_never_re_extracted(monkeypatch):
    """The whole point: a reader hits the store, not Tesseract."""
    monkeypatch.setattr(pt, "_extract", lambda path, page: ("stored body", True))
    doc_id = _doc(pages=1)
    with get_sessionmaker()() as session:
        pt.populate_document(session, doc_id, "/x.pdf", 1)

    def explode(path, page):
        raise AssertionError("re-extracted a page that was already stored")

    monkeypatch.setattr(pt, "_extract", explode)
    with get_sessionmaker()() as session:
        assert pt.get_page_text(session, doc_id, 1, pdf_path="/x.pdf") == "stored body"


def test_a_blank_page_is_stored_as_readable_but_empty(monkeypatch):
    """A film or separator sheet reads cleanly and carries no text. It must be recorded as OK, or a
    reviewer is told a page is unreadable when it is simply blank - and retrying it forever is waste."""
    monkeypatch.setattr(pt, "_extract", lambda path, page: ("", True))
    doc_id = _doc(pages=1)
    with get_sessionmaker()() as session:
        pt.populate_document(session, doc_id, "/x.pdf", 1)
        row = session.scalar(select(PageText).where(PageText.document_id == doc_id))
        assert row.extract_ok is True
        assert row.char_count == 0


def test_a_failed_page_is_recorded_as_failed_and_retried_on_read(monkeypatch):
    """WHEN extraction itself fails, THE SYSTEM SHALL record it as failed and retry it on a later read.

    A cached failure must not become permanent: an errored page is often a transient Tesseract timeout,
    which is exactly why the direct extraction path retries it too.
    """
    monkeypatch.setattr(pt, "_extract", lambda path, page: ("", False))
    doc_id = _doc(pages=1)
    with get_sessionmaker()() as session:
        pt.populate_document(session, doc_id, "/x.pdf", 1)
        row = session.scalar(select(PageText).where(PageText.document_id == doc_id))
        assert row.extract_ok is False

    # Later the page reads fine; the stored failure is replaced rather than served from cache.
    monkeypatch.setattr(pt, "_extract", lambda path, page: ("recovered", True))
    with get_sessionmaker()() as session:
        text, report = pt.get_row_text_with_report(session, doc_id, [1], pdf_path="/x.pdf")
        assert text == "recovered"
        assert report["errored"] == [] and report["blank"] == []


def test_a_tesseract_timeout_is_recorded_as_failed_not_blank(monkeypatch):
    """WHEN Tesseract times out on a page, THE SYSTEM SHALL record extract_ok=False.

    This test deliberately does NOT stub `pt._extract`, which every other test in this file does.
    That is exactly why the defect it pins survived: `_extract`'s own body is where the failure signal
    was lost, so stubbing it proves the CONTRACT while leaving the IMPLEMENTATION free to report
    ok=True for a page nobody managed to read. Stub below it - at the Tesseract call - and the real
    extraction path runs.
    """

    def timed_out(image, timeout=0, config=""):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", timed_out)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [object()])
    ocr._configured = True  # skip _ensure_tesseract's settings read

    text, ok = pt._extract("/nonexistent/synthetic.pdf", 1)
    assert text == ""
    assert ok is False, "a timed-out page must not be indistinguishable from a genuinely blank one"


def test_get_page_text_retries_a_page_stored_as_failed(monkeypatch):
    """WHEN a page stored with extract_ok=False is read with a pdf_path, THE SYSTEM SHALL re-extract.

    `get_row_text_with_report` already retries; this is the plain single-page read, which served the
    cached empty string forever instead. A cached failure must not become permanent on ANY read path.
    """
    monkeypatch.setattr(pt, "_extract", lambda path, page: ("", False))
    doc_id = _doc(pages=1)
    with get_sessionmaker()() as session:
        pt.populate_document(session, doc_id, "/x.pdf", 1)

    monkeypatch.setattr(pt, "_extract", lambda path, page: ("recovered", True))
    with get_sessionmaker()() as session:
        assert pt.get_page_text(session, doc_id, 1, pdf_path="/x.pdf") == "recovered"
        row = session.scalar(select(PageText).where(PageText.document_id == doc_id))
        assert row.extract_ok is True and row.char_count == len("recovered")


def test_populate_reattempts_a_page_stored_as_failed(monkeypatch):
    """WHEN population runs again over a page stored as failed, THE SYSTEM SHALL re-attempt it.

    `have` counted every stored page regardless of outcome, so one transient timeout removed a page
    from every future population of that document - the failure became permanent by omission.
    """
    monkeypatch.setattr(pt, "_extract", lambda path, page: ("", False))
    doc_id = _doc(pages=2)
    with get_sessionmaker()() as session:
        assert pt.populate_document(session, doc_id, "/x.pdf", 2) == 2

    monkeypatch.setattr(pt, "_extract", lambda path, page: (f"body{page}", True))
    with get_sessionmaker()() as session:
        assert pt.populate_document(session, doc_id, "/x.pdf", 2) == 2, (
            "failed pages must be retried"
        )
        rows = session.scalars(
            select(PageText).where(PageText.document_id == doc_id).order_by(PageText.page)
        ).all()
        assert [r.text for r in rows] == ["body1", "body2"]
        assert all(r.extract_ok for r in rows)


def test_the_row_report_separates_errored_from_blank(monkeypatch):
    """The contract the duplicate check depends on. Collapsing these is how a dedup run that could not
    read a fifth of a document once presented as a clean one."""
    outcomes = {1: ("real text", True), 2: ("", True), 3: ("", False)}
    monkeypatch.setattr(pt, "_extract", lambda path, page: outcomes[page])
    doc_id = _doc(pages=3)
    with get_sessionmaker()() as session:
        pt.populate_document(session, doc_id, "/x.pdf", 3)

    # pdf_path omitted so the errored page is NOT retried and its stored state is what is reported.
    with get_sessionmaker()() as session:
        text, report = pt.get_row_text_with_report(session, doc_id, [1, 2, 3])
    assert "real text" in text
    assert report["blank"] == [2]
    assert report["errored"] == [3]
    assert report["pages"] == [1, 2, 3]


def test_pages_are_joined_in_page_order_with_optional_markers(monkeypatch):
    """Depositions need `Page N:` markers to produce one summary line per page; every other category
    must NOT see them, or the markers reach the model input and the duplicate check's similarity
    scoring gains a shared vocabulary that makes unrelated documents look alike."""
    monkeypatch.setattr(pt, "_extract", lambda path, page: (f"body{page}", True))
    doc_id = _doc(pages=3)
    with get_sessionmaker()() as session:
        pt.populate_document(session, doc_id, "/x.pdf", 3)

    with get_sessionmaker()() as session:
        plain = pt.get_pages_text(session, doc_id, [3, 1, 2])
        marked = pt.get_pages_text(session, doc_id, [1, 2], mark_pages=True)
    assert plain == "body1body2body3"  # ascending, regardless of the order asked for
    assert marked == "Page 1:\nbody1\nPage 2:\nbody2\n"


@pytest.mark.parametrize("pages", [[], None])
def test_no_pages_requested_is_not_an_error(pages):
    doc_id = _doc(pages=1)
    with get_sessionmaker()() as session:
        assert pt.get_pages_text(session, doc_id, pages or []) == ""
        text, report = pt.get_row_text_with_report(session, doc_id, pages or [])
        assert text == "" and report["pages"] == []


def test_a_missing_tesseract_fails_fast_instead_of_marking_every_page_failed(monkeypatch):
    """WHEN Tesseract is MISSING, THE SYSTEM SHALL raise rather than record a per-page failure.

    The sibling test above pins the opposite case, and the PAIR is the point: a TIMEOUT is per-page and
    retryable, a MISSING BINARY is config and can never succeed. `ocr` draws that line deliberately and
    raises `OcrUnavailableError` to carry it; `_extract` caught it with everything else and flattened
    both to ("", False).

    That was survivable while a stored failure was permanent. It stopped being survivable when #131
    made failures RETRYABLE: with no binary present, every page of every document is marked failed and
    every later run re-attempts every page against something that cannot work. Stubbed below `_extract`
    for the same reason as the timeout test - the flattening happens inside its body, so stubbing
    `_extract` itself would prove nothing about it.
    """

    def not_installed(image, timeout=0, config=""):
        raise ocr.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", not_installed)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [object()])
    ocr._configured = True  # skip _ensure_tesseract's settings read

    with pytest.raises(OcrUnavailableError):
        pt._extract("/nonexistent/synthetic.pdf", 1)


def test_the_ocr_pool_size_comes_from_config_and_compose_agrees():
    """WHEN the OCR pass runs, THE SYSTEM SHALL size its pool from `page_text_workers`.

    Two things pinned, and the second is the one that bites. `populate_document` must take the pool
    size from config rather than a literal - and `docker-compose.yml` passes PAGE_TEXT_WORKERS
    explicitly, so a container reads the COMPOSE default and never the config one. Editing config
    alone changes nothing on a deployed box. `DUPE_SIMILARITY_OVERRIDE` was that exact bug in this
    tree - config said 0.99, compose said 0.90, and production served 0.90 from #81 - and it now has
    a guard of its own in `test_dedup.py`. This keeps the two in step for this setting.
    """
    import re
    from pathlib import Path

    from app.config import get_settings

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    match = re.search(r"PAGE_TEXT_WORKERS:\s*\$\{PAGE_TEXT_WORKERS:-(\d+)\}", text)
    assert match, "docker-compose.yml no longer passes PAGE_TEXT_WORKERS"
    assert int(match.group(1)) == get_settings().page_text_workers, (
        "docker-compose.yml and config.py disagree on the OCR pool size, so a deployed container "
        "would use the compose value and ignore the code default"
    )


def test_population_uses_the_configured_pool_size(monkeypatch):
    """The pool is sized from the setting, not from a literal in the function."""
    seen = {}
    real_pool = pt.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kw):
            seen["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kw)

    monkeypatch.setattr(pt, "ThreadPoolExecutor", _Spy)
    monkeypatch.setattr(pt, "_extract", lambda path, page: ("body", True))
    monkeypatch.setattr(pt.get_settings(), "page_text_workers", 5, raising=False)

    doc_id = _doc(pages=3)
    with get_sessionmaker()() as session:
        pt.populate_document(session, doc_id, "/x.pdf", 3)
    assert seen["max_workers"] == 5


def test_population_reports_progress_as_pages_land(monkeypatch):
    """DEMONSTRATES the bug: on origin/main `progress` does not exist and nothing is reported.

    Cooperative cancellation is observed only inside the job's `report`, and there are no model calls
    during OCR - so with the whole pass inside one blocking `pool.map`, nothing wrote progress and
    nothing polled the stop flag for its entire duration. On this repo's own measurement that is
    ~700s for a 297-page record: the bar read "reading 0 / N" and Stop did nothing, pushing the
    reviewer to Force stop, which SIGKILLs the work-horse for what should have been a cooperative
    stop.
    """
    monkeypatch.setattr(pt, "_extract", lambda path, page: (f"page {page}", True))
    doc_id = _doc(pages=5)
    seen = []

    with get_sessionmaker()() as session:
        added = pt.populate_document(
            session, doc_id, "/x.pdf", 5, workers=2, progress=lambda *a: seen.append(a)
        )

    assert added == 5
    assert len(seen) == 5, "one report per page, so the bar moves during the pass"
    assert {stage for stage, _, _ in seen} == {"reading"}
    # Monotonic and complete, whatever order the pool finishes in.
    assert [current for _, current, _ in seen] == [1, 2, 3, 4, 5]
    assert {total for _, _, total in seen} == {5}


def test_a_stop_during_the_ocr_pass_returns_without_draining_the_queue(monkeypatch):
    """DEMONSTRATES the other half, and it has to be TIMED to demonstrate anything.

    `report` raises JobCancelled on a stop, but every page is submitted to the pool up front and
    leaving the `with` block calls shutdown(wait=True) - cancel_futures defaults to False. So a
    version that merely lets the exception out still runs the ENTIRE remaining pass first: the stop
    is heard promptly and the job returns minutes later, which is the thing being fixed.

    An earlier version of this test asserted only `pytest.raises(JobCancelled)` and a stored count.
    Both hold whether or not the pool drains, because it stubbed `_extract` with an instant lambda
    and the drain is free when every page takes no time. The sleep is what gives the test teeth.

    Self-calibrating rather than absolute: it measures a FULL pass on this machine and requires the
    stopped one to be a fraction of it. A wall-clock threshold would be a flake on a loaded box.
    """
    import time

    from app.worker.failures import JobCancelled

    pages, per_page, workers = 60, 0.03, 4
    monkeypatch.setattr(
        pt, "_extract", lambda path, page: (time.sleep(per_page), (f"p{page}", True))[1]
    )

    # The reference: how long the whole pass takes here.
    baseline_doc = _doc(pages=pages)
    started = time.monotonic()
    with get_sessionmaker()() as session:
        pt.populate_document(session, baseline_doc, "/x.pdf", pages, workers=workers)
    full_pass = time.monotonic() - started

    def stop_after_four(stage, current, total):
        if current >= 4:
            raise JobCancelled(current, total)

    doc_id = _doc(pages=pages)
    started = time.monotonic()
    with get_sessionmaker()() as session, pytest.raises(JobCancelled):
        pt.populate_document(
            session, doc_id, "/x.pdf", pages, workers=workers, progress=stop_after_four
        )
    stopped = time.monotonic() - started

    assert stopped < full_pass / 3, (
        f"the stop returned in {stopped:.2f}s against a {full_pass:.2f}s full pass - the queued "
        "pages were still drained on the way out of the pool"
    )
    # Pages already read stay stored - the pass is idempotent, so a later run resumes. The few in
    # flight on the workers finish, so this is a range, not an exact count.
    with get_sessionmaker()() as session:
        stored = session.scalars(select(PageText.page).where(PageText.document_id == doc_id)).all()
    assert 0 < len(stored) < pages, "a stop must not store the whole document, nor discard the work"


def test_population_without_progress_still_works(monkeypatch):
    """GUARDS the default: `progress` is optional, so every other caller is unaffected."""
    monkeypatch.setattr(pt, "_extract", lambda path, page: (f"page {page}", True))
    doc_id = _doc(pages=3)
    with get_sessionmaker()() as session:
        assert pt.populate_document(session, doc_id, "/x.pdf", 3) == 3
