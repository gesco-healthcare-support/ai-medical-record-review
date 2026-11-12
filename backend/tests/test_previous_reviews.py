"""Entries an earlier review of the same patient already covered, highlighted in the record finished
last (2026-11-12): the page-content rule, the "finished last" order, the summarize run's hook, the
patient-ID change, the fold, and the yellow in the Word letter and the linked PDF.

Every record, page and summary here is synthetic: page texts are runs of NATO-alphabet words.
"""

import io
import random
import uuid
from datetime import datetime, timedelta

import pymupdf
import pytest
from docx import Document as WordDocument
from docx.enum.text import WD_COLOR_INDEX
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Document, Job, PageText, ReviewRow, Summary, User
from app.services import jobs, previous_reviews
from app.services.linked_pdf import _render_summary_pdf, _summary_html
from app.services.reporting import ReportDetails, build_mrr_document
from app.services.summarize_engine import fold_record_entries
from tests.conftest import unique_test_email

_VOCAB = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar "
    "papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu"
).split()
_T0 = datetime(2026, 10, 1, 9, 0)


def _page(seed: int, words: int = 60) -> str:
    """A synthetic page: ``words`` words drawn from a fixed list, the same for the same seed."""
    rng = random.Random(seed)
    return " ".join(rng.choice(_VOCAB) for _ in range(words))


def _new_id() -> str:
    return str(random.randrange(10**6, 10**7))


def _user(session) -> int:
    user = User(email=unique_test_email(), name="Reviewed", password="x", active=True)
    session.add(user)
    session.flush()
    return user.id


def _record(
    session,
    user_id: int,
    patient_id: str | None,
    pages: dict[int, str],
    entries: list[tuple[int, int, bool]],
    *,
    uploaded: datetime,
    finished: datetime | None,
) -> str:
    """A record with its page texts, one summary per ``(start, end, excluded)``, and a summarize run
    that finished at ``finished`` (None: never summarized). Returns its id."""
    doc_id = str(uuid.uuid4())
    session.add(
        Document(
            id=doc_id,
            user_id=user_id,
            original_filename="synthetic.pdf",
            stored_path="/nonexistent/synthetic.pdf",
            sha256="0" * 64,
            page_count=max(pages) if pages else 1,
            patient_id=patient_id,
            created_at=uploaded,
        )
    )
    session.flush()
    for page, text in pages.items():
        session.add(PageText(document_id=doc_id, page=page, text=text, char_count=len(text)))
    job = Job(
        document_id=doc_id,
        kind="summarize",
        # Never an ACTIVE state: one would block a real job on the record. A run that has not
        # finished is a done job with no finish time, which is what `summarized_at` reads as none.
        state="done",
        model="m",
        prompt_version="1",
        finished_at=finished,
    )
    session.add(job)
    session.flush()
    for idx, (start, end, excluded) in enumerate(entries):
        session.add(
            Summary(
                document_id=doc_id,
                job_id=job.id,
                idx=idx,
                title=f"ENTRY {idx}",
                date="01/02/2026",
                text="A synthetic summary.",
                row_start=start,
                row_end=end,
                row_category="1",
                excluded=excluded,
            )
        )
    session.flush()
    return doc_id


def _marks(session, doc_id: str) -> list[datetime | None]:
    return [
        s.reviewed_before
        for s in session.scalars(
            select(Summary).where(Summary.document_id == doc_id).order_by(Summary.idx)
        ).all()
    ]


def _mark(session, doc_id: str, done_at: datetime) -> int:
    return previous_reviews.mark_reviewed_before(
        session, session.get(Document, doc_id), done_at=done_at
    )


# --- the rule -----------------------------------------------------------------------------------


def test_an_entry_whose_pages_an_earlier_review_summarized_is_marked_and_a_new_one_is_not():
    """DEMONSTRATES the request: pages 1-2 were in the earlier record's summarized entry, page 3
    is new - so the first entry is marked, with the earlier record's upload time, and the second
    is not."""
    pid = _new_id()
    with get_sessionmaker()() as session:
        user = _user(session)
        earlier = _record(
            session,
            user,
            pid,
            {5: _page(1), 6: _page(2)},
            [(5, 6, False)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=1),
        )
        later = _record(
            session,
            user,
            pid,
            {1: _page(1), 2: _page(2), 3: _page(3)},
            [(1, 2, False), (3, 3, False)],
            uploaded=_T0 + timedelta(days=5),
            finished=None,
        )
        assert earlier
        assert _mark(session, later, _T0 + timedelta(days=6)) == 1
        assert _marks(session, later) == [_T0, None]
        session.rollback()


def test_an_entry_with_one_new_page_is_not_marked():
    """GUARD: an editor may skip a highlighted entry, so one page the earlier review did not have
    keeps the whole entry unmarked."""
    pid = _new_id()
    with get_sessionmaker()() as session:
        user = _user(session)
        _record(
            session,
            user,
            pid,
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=1),
        )
        later = _record(
            session,
            user,
            pid,
            {1: _page(1), 2: _page(9)},
            [(1, 2, False)],
            uploaded=_T0 + timedelta(days=5),
            finished=None,
        )
        assert _mark(session, later, _T0 + timedelta(days=6)) == 0
        assert _marks(session, later) == [None]
        session.rollback()


def test_pages_the_earlier_reviewer_left_out_of_the_export_do_not_count():
    """GUARD: an entry excluded from the earlier export was not reviewed there."""
    pid = _new_id()
    with get_sessionmaker()() as session:
        user = _user(session)
        _record(
            session,
            user,
            pid,
            {1: _page(1)},
            [(1, 1, True)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=1),
        )
        later = _record(
            session,
            user,
            pid,
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0 + timedelta(days=5),
            finished=None,
        )
        assert _mark(session, later, _T0 + timedelta(days=6)) == 0
        session.rollback()


def test_an_entry_with_no_readable_page_is_never_marked():
    """GUARD: a page with too little text (a hand-filled form) cannot be compared - even when its
    few words are the same in both records."""
    pid = _new_id()
    with get_sessionmaker()() as session:
        user = _user(session)
        _record(
            session,
            user,
            pid,
            {1: _page(5, 12)},
            [(1, 1, False)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=1),
        )
        later = _record(
            session,
            user,
            pid,
            {1: _page(5, 12)},
            [(1, 1, False)],
            uploaded=_T0 + timedelta(days=5),
            finished=None,
        )
        assert _mark(session, later, _T0 + timedelta(days=6)) == 0
        session.rollback()


def test_a_page_that_only_partly_matches_is_not_the_same_page():
    """GUARD: under MATCH_SHARE of a page's runs found on one earlier page is a different page."""
    pid = _new_id()
    first, rest = _page(1, 30), _page(2, 40)
    with get_sessionmaker()() as session:
        user = _user(session)
        _record(
            session,
            user,
            pid,
            {1: first + " " + _page(3, 40)},
            [(1, 1, False)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=1),
        )
        later = _record(
            session,
            user,
            pid,
            {1: first + " " + rest},
            [(1, 1, False)],
            uploaded=_T0 + timedelta(days=5),
            finished=None,
        )
        assert _mark(session, later, _T0 + timedelta(days=6)) == 0
        session.rollback()


def test_another_patients_record_never_counts():
    """GUARD: the same pages under a different patient ID, or a record with no ID, mark nothing."""
    with get_sessionmaker()() as session:
        user = _user(session)
        _record(
            session,
            user,
            _new_id(),
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=1),
        )
        later = _record(
            session,
            user,
            _new_id(),
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0 + timedelta(days=5),
            finished=None,
        )
        no_id = _record(
            session,
            user,
            None,
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0 + timedelta(days=5),
            finished=None,
        )
        assert _mark(session, later, _T0 + timedelta(days=6)) == 0
        assert _mark(session, no_id, _T0 + timedelta(days=6)) == 0
        session.rollback()


def test_the_record_finished_last_is_the_one_marked_whatever_was_uploaded_first():
    """DEMONSTRATES "whatever gets done last": A is uploaded first but summarized after B, so A's
    entries are marked against B - and B, done first, is not marked against A."""
    pid = _new_id()
    with get_sessionmaker()() as session:
        user = _user(session)
        a = _record(
            session,
            user,
            pid,
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=9),
        )
        b = _record(
            session,
            user,
            pid,
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0 + timedelta(days=2),
            finished=_T0 + timedelta(days=3),
        )
        assert _mark(session, b, _T0 + timedelta(days=3)) == 0
        assert _mark(session, a, _T0 + timedelta(days=9)) == 1
        assert _marks(session, a) == [_T0 + timedelta(days=2)]
        # Without done_at, the record's own last summarize run is the cut-off.
        assert previous_reviews.mark_reviewed_before(session, session.get(Document, b)) == 0
        session.rollback()


# --- the summarize run, the patient ID, the re-draft ------------------------------------------


def test_a_summarize_run_marks_its_entries_when_it_ends(monkeypatch):
    """DEMONSTRATES the hook: a summarize run ends with its record's entries marked against the
    patient's earlier-finished record."""
    import app.services.summarize_engine as se
    from app.worker.tasks import summarize_document

    monkeypatch.setattr(
        se,
        "summarize_row",
        lambda pdf_path, row, model=None, prompt=None, standalone_studies=None, **_kw: {
            "summaryTitle": "ENTRY",
            "summaryDate": "01/02/2026",
            "summaryText": "A synthetic summary.",
            "manualCheck": "",
            "sourceText": "x",
        },
    )
    pid = _new_id()
    with get_sessionmaker()() as session:
        user = _user(session)
        _record(
            session,
            user,
            pid,
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=1),
        )
        later = _record(
            session,
            user,
            pid,
            {1: _page(1), 2: _page(7)},
            [],
            uploaded=_T0 + timedelta(days=5),
            finished=None,
        )
        for idx, page in enumerate((1, 2)):
            session.add(
                ReviewRow(
                    document_id=later,
                    idx=idx,
                    start=page,
                    end=page,
                    category="1",
                    title="A",
                    date="01/02/2026",
                    injury_date="-",
                    flag="-",
                    include=True,
                )
            )
        session.commit()
        job_id = jobs.create_job(session, later, "summarize", model="m", prompt_version="1").id

    summarize_document(job_id)
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        marks = _marks(session, later)
    assert marks == [_T0, None]


def test_a_failure_while_marking_never_fails_the_run(monkeypatch, caplog):
    """GUARD: the summaries are stored before the marking; an error there is logged by type only."""
    from app.worker import tasks

    def boom(*_a, **_kw):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(previous_reviews, "mark_reviewed_before", boom)
    with get_sessionmaker()() as session:
        user = _user(session)
        doc_id = _record(
            session, user, _new_id(), {1: _page(1)}, [(1, 1, False)], uploaded=_T0, finished=None
        )
        session.commit()
        tasks._mark_reviewed_before(session, doc_id)
    assert "RuntimeError" in caplog.text


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture
async def authed(client, seeded_user):
    email, password = seeded_user
    resp = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 204
    with get_sessionmaker()() as session:
        user_id = session.scalar(select(User.id).where(User.email == email))
    return client, user_id


async def test_giving_the_patient_id_by_hand_marks_the_entries_and_the_listing_says_so(authed):
    """DEMONSTRATES: a record summarized without its ID gets its marks once a reviewer types it,
    and the Summaries list carries `reviewedBefore` (the earlier record's upload time)."""
    client, user_id = authed
    pid = _new_id()
    with get_sessionmaker()() as session:
        _record(
            session,
            user_id,
            pid,
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0,
            finished=_T0 + timedelta(days=1),
        )
        later = _record(
            session,
            user_id,
            None,
            {1: _page(1)},
            [(1, 1, False)],
            uploaded=_T0 + timedelta(days=5),
            finished=_T0 + timedelta(days=6),
        )
        session.commit()

    before = (await client.get(f"/api/documents/{later}/summaries")).json()
    assert before[0]["reviewedBefore"] is None
    resp = await client.put(f"/api/documents/{later}/patient-id", json={"patient_id": pid})
    assert resp.status_code == 200, resp.text
    after = (await client.get(f"/api/documents/{later}/summaries")).json()
    assert after[0]["reviewedBefore"] == "2026-10-01T09:00:00+00:00"


# --- the deliverables -------------------------------------------------------------------------


def _entry(title: str, *, seen: bool, category_date: str = "01/02/2026") -> dict:
    entry = {
        "summaryDate": category_date,
        "summaryTitle": title,
        "summaryText": "A synthetic summary of the visit.",
        "diagnostic": False,
    }
    return entry | ({"reviewedBefore": True} if seen else {})


def test_a_folded_entry_is_reviewed_before_only_when_every_piece_was():
    """GUARD: one new piece folded into a visit makes the whole entry new."""
    entries = [
        _entry("DR EXAMPLE, PR-2", seen=True),
        _entry("DR EXAMPLE, PR-2", seen=False),
        _entry("DR OTHER, PR-2", seen=True),
    ]
    folded = fold_record_entries(entries, ["1", "1", "1"], "summaryTitle")
    assert [e.get("reviewedBefore", False) for e in folded] == [False, True]
    both = fold_record_entries(
        [_entry("DR EXAMPLE, PR-2", seen=True), _entry("DR EXAMPLE, PR-2", seen=True)],
        ["1", "1"],
        "summaryTitle",
    )
    assert [e.get("reviewedBefore") for e in both] == [True]


def _highlights(docx_bytes: bytes) -> list[bool]:
    """Per entry row of the letter's table: is every run of it highlighted yellow?"""
    document = WordDocument(io.BytesIO(docx_bytes))
    rows = document.tables[0].rows
    out = []
    for row in rows:
        runs = [r for cell in row.cells for p in cell.paragraphs for r in p.runs if r.text.strip()]
        out.append(
            bool(runs) and all(r.font.highlight_color == WD_COLOR_INDEX.YELLOW for r in runs)
        )
    return out


def test_the_word_letter_highlights_an_entry_reviewed_before_in_yellow():
    """DEMONSTRATES: the Word letter's reviewed-before entry is yellow throughout - date, title and
    body - and the other entry is not highlighted at all."""
    document = build_mrr_document(
        [_entry("FIRST", seen=True), _entry("SECOND", seen=False, category_date="02/02/2026")],
        10,
        "Synthetic Patient",
        "01/01/1980",
        "",
        details=ReportDetails(),
    )
    buffer = io.BytesIO()
    document.save(buffer)
    assert _highlights(buffer.getvalue()) == [True, False]


def test_the_linked_pdf_highlights_an_entry_reviewed_before_in_yellow():
    """DEMONSTRATES: the PDF letter draws yellow behind the reviewed-before entry only."""

    def yellow_fills(entries) -> int:
        details = ReportDetails()
        pdf, _ = _render_summary_pdf(_summary_html(entries, 10, "", details))
        return sum(
            1
            for page in pdf
            for d in page.get_drawings()
            if d.get("fill") and tuple(round(c, 2) for c in d["fill"]) == (1.0, 1.0, 0.0)
        )

    pdf_entry = {
        "summaryDate": "01/02/2026",
        "linkTitle": "FIRST",
        "summaryText": "A synthetic summary of the visit.",
        "startPage": 1,
        "diagnostic": False,
    }
    assert yellow_fills([pdf_entry]) == 0
    assert yellow_fills([pdf_entry | {"reviewedBefore": True}]) > 0
    assert pymupdf  # the renderer under test
