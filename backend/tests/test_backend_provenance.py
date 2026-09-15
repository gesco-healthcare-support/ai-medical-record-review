"""Backend provenance: WHICH BACKEND answered, recorded beside which model did.

T9 shipped this wiring unverified, which is the debt this file pays. The column exists on both
`jobs` and `summaries` (migration b3e9f0c47a15) and is written in four places; nothing pinned any
of them, and `grep -rn '\\.backend' backend/tests/` returned nothing before this file.

THE FAILURE THIS IS REALLY GUARDING is silent, not loud. Every read site uses `output.get("backend")`,
so a producer that stopped emitting the key would not raise - it would write NULL for every row, and
NULL is a legitimate value here (it means "written before the column existed"). A backend that never
records itself is indistinguishable from a backfill gap, which is exactly the state the column was
added to escape.

A REAL ``Settings`` IS USED AND THE GLOBAL CACHE IS LEFT ALONE. Where a test needs different config
it patches the attribute on the cached instance (monkeypatch restores it), mirroring
test_jobs.py:243. Calling ``get_settings.cache_clear()`` here would rebuild settings from whatever
environment happened to be live - an earlier file did that with a fake DATABASE_URL and produced a
foreign-key violation in an unrelated test's teardown, several files away.

``get_provider`` is ``lru_cache``d, so changing config does NOT change the provider a row reports.
The row-level tests therefore patch ``se.get_provider`` directly rather than reaching for
``cache_clear()`` - same reason: it is shared state other tests are entitled to.
"""

import uuid

from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Document, Job, ReviewRow, Summary, User
from app.services import jobs
from app.services import summarize_engine as se
from app.worker.tasks import summarize_document
from tests.conftest import unique_test_email


def _make_user_and_doc(page_count: int = 1) -> str:
    """Insert a test user + document (no file on disk needed); return the document id.

    Mirrors test_jobs.py's helper. Duplicated rather than imported because importing across test
    modules couples their fixtures - the pytest-auth- prefix is what conftest cleans up, and that is
    the only contract that matters here.
    """
    with get_sessionmaker()() as session:
        user = User(
            email=unique_test_email(),
            name="Provenance",
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


def _row(**over):
    """One segmentation row in the shape summarize_row and _unreadable_output both read."""
    row = {
        "start": 1,
        "end": 1,
        "category": "1",
        "date": "2026-01-01",
        "injury_date": "-",
        "flag": "",
        "title": "Progress Note",
    }
    row.update(over)
    return row


def _add_review_row(doc_id: str) -> None:
    """One included ReviewRow, which is what summarize_document iterates."""
    with get_sessionmaker()() as session:
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
            )
        )
        session.commit()


class _Provider:
    """A stand-in for whatever provider answered. Only `name` is read by _backend_name."""

    def __init__(self, name: str):
        self.name = name


# --- the job column ---------------------------------------------------------------------------


def test_a_summarize_job_records_the_backend_it_started_with(monkeypatch):
    # WHEN a summarize job is created, THE SYSTEM SHALL record the backend that will answer it.
    # Resolved once at creation for the same reason the models are: a job resumed after a config
    # change must keep the backend it started with, or one delivered document is written by two
    # vendors with nothing saying which wrote what.
    monkeypatch.setattr(get_settings(), "llm_backend", "vllm")
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1")
        assert job.backend == "vllm"


def test_a_job_that_does_not_cross_the_seam_records_no_backend(monkeypatch):
    # WHEN a job of any other kind is created, THE SYSTEM SHALL leave the backend NULL.
    # This pins a DELIBERATE PR-1 decision rather than an oversight: segment/classify/dedup still
    # call google-genai directly and do not cross the provider seam until PR 2 and PR 3, so a stamp
    # here would record an intention, not an observation. A column saying "vllm" about a call that
    # went to Gemini is worse than one saying nothing.
    #
    # It is also the tripwire for PR 2: routing a service through get_provider() without stamping it
    # leaves this passing while the new rows go unattributed, so this test must be revisited by name
    # when each service moves.
    monkeypatch.setattr(get_settings(), "llm_backend", "vllm")
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1")
        assert job.backend is None


def test_a_per_stage_override_reaches_the_job_column(monkeypatch):
    # WHEN summarize is overridden to a different backend, THE SYSTEM SHALL record the OVERRIDE.
    # A single per-stage override is enough to send records to a different vendor, so a stamp
    # reading only the global setting would record the wrong one while the traffic went elsewhere -
    # the same fail-open shape the config guards are keyed on resolved_backends() to avoid.
    monkeypatch.setattr(get_settings(), "llm_backend", "gemini")
    monkeypatch.setattr(get_settings(), "llm_backend_overrides", "summarize=vllm")
    doc_id = _make_user_and_doc()
    with get_sessionmaker()() as session:
        job = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1")
        assert job.backend == "vllm"


# --- the row output ---------------------------------------------------------------------------


def _stub_calls(monkeypatch, provider_name: str):
    """Run summarize_row with every model call and the OCR read stubbed out."""
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: (
            "raw OCR",
            {"pages": len(pages), "errored": [], "blank": []},
        ),
    )
    monkeypatch.setattr(se, "_generate", lambda *a, **k: ("text", False))
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda *a, **k: {"fixed_text": "text", "fixed_title": "T", "issues": []},
    )
    monkeypatch.setattr(se, "get_provider", lambda *a, **k: _Provider(provider_name))


def test_a_summarized_row_records_the_backend_that_served_it(monkeypatch):
    # WHEN a row is summarized, THE SYSTEM SHALL report the backend that answered its calls, so the
    # caller can persist it. One value covers body, title and audit: all three are the summarize
    # stage, so they resolve to the same backend.
    _stub_calls(monkeypatch, "vllm")
    out = se.summarize_row("/x.pdf", _row(), model="m", prompt="P", verify=True)
    assert out["backend"] == "vllm"


def test_the_row_records_the_provider_that_answered_not_the_configured_one(monkeypatch):
    # WHEN config and the serving provider disagree, THE SYSTEM SHALL record the PROVIDER.
    # _backend_name reads off the provider deliberately, so a row says what happened rather than
    # what the settings happened to say when somebody reads them later. Config is set to gemini here
    # and the provider to vllm: an implementation that read config would pass every other test in
    # this file and fail only this one.
    monkeypatch.setattr(get_settings(), "llm_backend", "gemini")
    _stub_calls(monkeypatch, "vllm")
    out = se.summarize_row("/x.pdf", _row(), model="m", prompt="P", verify=True)
    assert out["backend"] == "vllm"


def test_a_row_nothing_could_be_read_from_records_no_backend():
    # WHEN no model saw a row, THE SYSTEM SHALL record no backend.
    # Asserted as `is None` rather than falsy on purpose: the failure worth catching is a default
    # vendor name appearing here, which would read as a fact about where the row went when nothing
    # went anywhere. NULL beside model=None is what tells a notice row from a summarized one.
    out = se._unreadable_output(_row(), [1])
    assert out["backend"] is None
    assert out["model"] is None


# --- persistence: the row output actually reaching the column -----------------------------------


def _stub_summarize_row(monkeypatch, backend):
    """Replace summarize_row wholesale with a fixed output carrying `backend`."""
    monkeypatch.setattr(
        se,
        "summarize_row",
        lambda pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw: {
            "summaryTitle": "T (Pages 1-1)",
            "summaryDate": "-",
            "summaryText": "body",
            "manualCheck": "",
            "sourceText": "x",
            "backend": backend,
        },
    )


def test_the_backend_a_row_reports_reaches_the_summary_column(monkeypatch):
    # WHEN a summary row is persisted, THE SYSTEM SHALL store the backend its output reported.
    # This is the join the other tests cannot see. Both ends can be correct while the hand-off
    # drops the value, and because the read side is `output.get("backend")` that drop is silent -
    # every row would simply be NULL, which is a legitimate value meaning something else entirely.
    _stub_summarize_row(monkeypatch, "vllm")
    doc_id = _make_user_and_doc()
    _add_review_row(doc_id)
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id

    summarize_document(job_id)
    with get_sessionmaker()() as session:
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1
        assert summaries[0].backend == "vllm"


def test_a_row_that_reports_no_backend_stores_none(monkeypatch):
    # WHEN a row reports no backend, THE SYSTEM SHALL store NULL rather than substituting one.
    # The mirror of the test above: persistence must not invent a default on the way in, or the
    # notice-row case stops being distinguishable from a real attribution.
    _stub_summarize_row(monkeypatch, None)
    doc_id = _make_user_and_doc()
    _add_review_row(doc_id)
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id

    summarize_document(job_id)
    with get_sessionmaker()() as session:
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert len(summaries) == 1
        assert summaries[0].backend is None


def test_the_job_and_its_rows_agree_on_the_backend(monkeypatch):
    # WHEN a job runs to completion, THE SYSTEM SHALL record the same backend on the job and on the
    # rows it produced. The two are written from DIFFERENT sources - the job from config at creation
    # time, each row from the provider that served it - and that is intentional, so they can diverge
    # if config moves mid-job. Under a stable config they must agree, and a disagreement here is the
    # signal that a job spanned two vendors.
    monkeypatch.setattr(get_settings(), "llm_backend", "vllm")
    _stub_summarize_row(monkeypatch, "vllm")
    doc_id = _make_user_and_doc()
    _add_review_row(doc_id)
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id

    summarize_document(job_id)
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        summaries = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert job.backend == "vllm"
        assert [s.backend for s in summaries] == ["vllm"]
