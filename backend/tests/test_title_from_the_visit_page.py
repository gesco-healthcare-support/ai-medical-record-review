"""A visit row that opens on an RFA or a work status is titled from the visit's page.

The summary's title call reads a row's first page. When a reviewer merges a work status or an RFA
with the visit after it, that first page is the form, and the title named the form: measured
read-only on the live box (2026-11-24, 281 such rows, real Gemini title calls), 97 titles named the
form when the call read the row's first page and 12 when its first image was the visit's page.
`visit_merge.visit_page_after_a_form` finds that page from the machine's draft pieces,
`seed_title_pages` hands it down as ``title_page``, and `summarize_engine._title_contents` reads it.
Synthetic rows only; no model call.
"""

import uuid

import pytest
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Document, Job, ReviewRow, SegmentRow, Summary, User
from app.services import summarize_engine as se
from app.services import visit_merge
from tests.conftest import unique_test_email

_DATE = "06/17/2026"


def _piece(start, end, category="1", title="HISTORY AND PHYSICAL", date=_DATE):
    return {"start": start, "end": end, "category": category, "title": title, "date": date}


_RFA = _piece(1, 1, "10", "REQUEST FOR AUTHORIZATION")
_WORK_STATUS = _piece(1, 1, "1", "WORK STATUS REPORT")
_VISIT = _piece(2, 5)


def _row(start, end, category="1"):
    return {"start": start, "end": end, "category": category, "title": "H AND P", "date": _DATE}


# --- the rule ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pieces", "row", "expected"),
    [
        ([_RFA, _VISIT], _row(1, 5), 2),  # the reported case: the RFA, then the visit
        ([_WORK_STATUS, _VISIT], _row(1, 5), 2),  # a work status filed 1
        ([_RFA, _piece(2, 2, "100", "WORK STATUS"), _piece(3, 5)], _row(1, 5), 3),  # two forms
        ([_RFA, _VISIT], _row(1, 5, "2"), 2),  # a PR-4
        ([_RFA, _VISIT], _row(1, 5, "5"), 2),  # a therapy note
    ],
    ids=["rfa", "work status", "two forms", "pr-4", "therapy"],
)
def test_a_visit_row_that_opens_on_a_form_is_titled_from_the_visit(pieces, row, expected):
    """DEMONSTRATES: the first page of the first piece after the form(s)."""
    assert visit_merge.visit_page_after_a_form(row, pieces) == expected


@pytest.mark.parametrize(
    ("pieces", "row"),
    [
        (
            [_piece(1, 3), _piece(4, 4, "10", "REQUEST FOR AUTHORIZATION")],
            _row(1, 4),
        ),  # visit first
        (
            [_piece(1, 2), _piece(3, 5)],
            _row(1, 5),
        ),  # one visit in two pieces: it opens on the visit
        ([_RFA, _VISIT], _row(1, 1, "10")),  # the RFA alone
        ([_RFA, _VISIT], _row(1, 5, "10")),  # a row the reviewer kept as the RFA
        ([_RFA, _VISIT], _row(1, 5, "3")),  # a diagnostic study
        ([_piece(1, 5, "1", "WORK STATUS AND PR-2")], _row(1, 5)),  # one piece: nothing to go on
        ([_RFA, _VISIT], _row(2, 5)),  # a row the reviewer started on the visit
        ([_RFA, _piece(2, 2, "100", "WORK STATUS")], _row(1, 2)),  # forms only
        ([], _row(1, 5)),  # no draft
    ],
    ids=[
        "visit first",
        "visit in two pieces",
        "rfa alone",
        "kept as rfa",
        "study",
        "one piece",
        "starts on visit",
        "forms only",
        "no draft",
    ],
)
def test_every_other_row_keeps_its_first_page(pieces, row):
    """GUARD: the title call's first image moves only for a visit row opening on a form piece."""
    assert visit_merge.visit_page_after_a_form(row, pieces) is None


# --- the title call ----------------------------------------------------------------------------


def _images(monkeypatch):
    pages = []

    def fake(_path, start, end, cap):
        pages.append((start, end, cap))
        return [f"IMG{start}"]

    monkeypatch.setattr(se, "page_image_parts", fake)
    monkeypatch.setattr(get_settings(), "summary_multimodal", True)
    return pages


def test_the_title_call_reads_the_visit_page_and_the_last_page(monkeypatch):
    """DEMONSTRATES: the first image is ``title_page``; the last page and the text are unchanged."""
    pages = _images(monkeypatch)
    contents = se._title_contents("/x.pdf", {**_row(1, 5), "title_page": 2}, "OCR")
    assert pages == [(2, 2, 1), (5, 5, 1)]
    assert contents[:2] == ["IMG2", "IMG5"]
    assert contents[2].text.endswith("OCR")


@pytest.mark.parametrize("title_page", [None, 1, 6, 0, "2"], ids=str)
def test_a_title_page_outside_the_row_is_ignored(monkeypatch, title_page):
    """GUARD: without a usable ``title_page`` the call reads the row's first page, as before."""
    pages = _images(monkeypatch)
    row = _row(1, 5) if title_page is None else {**_row(1, 5), "title_page": title_page}
    se._title_contents("/x.pdf", row, "OCR")
    assert pages == [(1, 1, 1), (5, 5, 1)]


def test_a_one_page_visit_after_the_form_is_sent_once(monkeypatch):
    """GUARD: when the visit's page is the row's last, one image, not the same page twice."""
    pages = _images(monkeypatch)
    se._title_contents("/x.pdf", {**_row(1, 2), "title_page": 2}, "OCR")
    assert pages == [(2, 2, 1)]


# --- handed down from the database ---------------------------------------------------------------


def _document(user_id=None, pieces=(_RFA, _VISIT, _piece(6, 6, "3", "MRI OF THE KNEE"))) -> str:
    with get_sessionmaker()() as session:
        if user_id is None:
            user = User(
                email=unique_test_email(),
                name="Title page",
                password=MrrPasswordHelper().hash("Str0ng#pw1"),
                active=True,
            )
            session.add(user)
            session.flush()
            user_id = user.id
        document = Document(
            id=str(uuid.uuid4()),
            user_id=user_id,
            original_filename="synthetic.pdf",
            stored_path="/nonexistent/synthetic.pdf",
            sha256="0" * 64,
            page_count=6,
        )
        session.add(document)
        session.flush()
        job = Job(
            document_id=document.id, kind="segment", state="done", model="m", prompt_version="v"
        )
        session.add(job)
        session.flush()
        for idx, piece in enumerate(pieces):
            session.add(SegmentRow(job_id=job.id, idx=idx, **piece))
        for idx, (start, end, category) in enumerate([(1, 5, "1"), (6, 6, "3")]):
            session.add(
                ReviewRow(
                    document_id=document.id,
                    idx=idx,
                    start=start,
                    end=end,
                    category=category,
                    title="H AND P",
                    date=_DATE,
                    include=True,
                )
            )
        session.commit()
        return document.id


def test_rows_get_the_visit_page_from_the_newest_draft():
    """DEMONSTRATES `seed_title_pages`: the reviewer's merged row gets its visit's page; the study
    gets nothing."""
    doc_id = _document()
    rows = [_row(1, 5), _row(6, 6, "3")]
    with get_sessionmaker()() as session:
        visit_merge.seed_title_pages(session, doc_id, rows)
    assert rows[0]["title_page"] == 2
    assert "title_page" not in rows[1]


def test_a_record_without_a_draft_is_left_alone():
    """GUARD: no done segment job, no pieces, no ``title_page``."""
    doc_id = _document(pieces=())
    with get_sessionmaker()() as session:
        session.execute(Job.__table__.delete().where(Job.document_id == doc_id))
        session.commit()
        rows = [_row(1, 5)]
        visit_merge.seed_title_pages(session, doc_id, rows)
    assert "title_page" not in rows[0]


def _capturing(seen):
    def fake(_pdf_path, row, *_a, **_kw):
        seen.append(dict(row))
        return {
            "summaryTitle": "T (Pages 1-5)",
            "summaryDate": "-",
            "summaryText": "body",
            "manualCheck": "",
            "sourceText": "x",
        }

    return fake


def test_a_summarize_run_hands_the_visit_page_down(monkeypatch):
    """DEMONSTRATES the wiring: the run seeds ``title_page`` before the rows go to the pool."""
    from app.services import jobs
    from app.worker.tasks import summarize_document

    seen = []
    monkeypatch.setattr(se, "summarize_row", _capturing(seen))
    doc_id = _document()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "summarize", model="m", prompt_version="1").id
    summarize_document(job_id)
    by_start = {row["start"]: row for row in seen}
    assert by_start[1]["title_page"] == 2
    assert "title_page" not in by_start[6]


@pytest.fixture
async def authed(client, seeded_user):
    email, password = seeded_user
    resp = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 204
    with get_sessionmaker()() as session:
        return client, session.scalar(select(User.id).where(User.email == email))


async def test_a_re_draft_hands_the_visit_page_down(authed, monkeypatch):
    """DEMONSTRATES the wiring of Re-draft (D16), which calls summarize_row itself."""
    import app.api.documents as documents_module

    client, user_id = authed
    doc_id = _document(user_id=user_id)
    with get_sessionmaker()() as session:
        job = Job(document_id=doc_id, kind="summarize", state="done", model="m", prompt_version="1")
        session.add(job)
        session.flush()
        session.add(
            Summary(
                document_id=doc_id,
                job_id=job.id,
                idx=0,
                title="old",
                text="old",
                row_start=1,
                row_end=5,
                row_category="1",
            )
        )
        session.commit()
    seen = []
    monkeypatch.setattr(documents_module, "summarize_row", _capturing(seen))
    resp = await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")
    assert resp.status_code == 200
    assert seen[0]["title_page"] == 2


async def test_a_bundle_summary_hands_the_visit_page_down(authed, monkeypatch):
    """DEMONSTRATES the wiring of the bundle's on-demand summaries, which call summarize_row too."""
    client, user_id = authed
    doc_id = _document(user_id=user_id)
    seen = []
    monkeypatch.setattr(se, "summarize_row", _capturing(seen))
    await client.post(f"/api/documents/{doc_id}/bundle/summarize", json={"categories": ["1"]})
    assert [row["start"] for row in seen] == [1]
    assert seen[0]["title_page"] == 2
