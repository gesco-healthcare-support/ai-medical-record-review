"""One visit's pieces merged automatically, with the machine's pieces kept for "Split back"
(`visit_merge.auto_merge_visit_pieces`, AUTO_MERGE_VISIT_PIECES).

The senior reviewer, 2026-11-05, on applying the visit-piece merges automatically: "I would say yes,
it is fairly rare that they are wrong so it would save a lot of time." Synthetic rows only; no model
call is possible here (the rule makes none).
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import Document, Job, ReviewRow, SegmentRow, User
from app.services import jobs, visit_merge
from tests.conftest import unique_test_email

_DATE = "02/04/2026"


def _row(start, category="1", title="PR-2 PROGRESS REPORT", date=_DATE, end=None, **over):
    return {
        "start": start,
        "end": start if end is None else end,
        "category": category,
        "title": title,
        "date": date,
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
        "method": "rules",
        **over,
    }


_WORK_STATUS = "WORK STATUS REPORT"
_RFA = "REQUEST FOR AUTHORIZATION"
_THERAPY = "PHYSICAL THERAPY NOTE"


def _spans(rows):
    return [(r["start"], r["end"], r["category"]) for r in rows]


# --- the rule ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rows",
    [
        [_row(1), _row(2)],  # one PR-2 cut in two
        [_row(1, "5", _THERAPY), _row(2, "5", _THERAPY)],  # one therapy visit cut in two
        [_row(1), _row(2, "10", _RFA)],  # the visit's RFA
        [_row(1, "10", _RFA), _row(2)],  # the RFA first
        [_row(1), _row(2, "100", _WORK_STATUS)],  # the visit's work status, whatever its category
    ],
    ids=["1+1", "5+5", "1+rfa", "rfa+1", "1+work status"],
)
def test_one_visits_pieces_are_merged_with_no_model_call(monkeypatch, rows):
    """DEMONSTRATES: the visit merge's candidates, merged without asking the model."""

    def _no_model(*_a, **_k):
        raise AssertionError("the automatic merge must not call the model")

    monkeypatch.setattr(visit_merge, "_same_author", _no_model)
    out, stats = visit_merge.auto_merge_visit_pieces(rows)
    assert [(r["start"], r["end"]) for r in out] == [(1, 2)]
    assert stats == {"runs": 1, "merged": 1}


def test_the_merged_row_is_the_visit_documents():
    """DEMONSTRATES the collapse: the visit's category, title and method, the first piece's start
    and hint, the last piece's end, flagged when any piece was."""
    rows = [
        _row(1, "10", _RFA, method="llm+embedding", suggest_merge=True),
        _row(2, title="PR-2 PROGRESS REPORT", method="rules", end=4, flag="x"),
        _row(5, "100", _WORK_STATUS, method="rules"),
    ]
    (merged,), _ = visit_merge.auto_merge_visit_pieces(rows)
    assert (merged["start"], merged["end"]) == (1, 5)
    assert (merged["category"], merged["title"], merged["method"]) == (
        "1",
        "PR-2 PROGRESS REPORT",
        "rules",
    )
    assert merged["suggest_merge"] is True
    assert merged["flag"] == "x"
    assert rows[0]["end"] == 1, "the caller's rows must come back untouched"


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        # 1 | work status | 5: the work status goes with the PR-2, the therapy note stays apart
        (
            [_row(1), _row(2, "100", _WORK_STATUS), _row(3, "5", _THERAPY)],
            [(1, 2, "1"), (3, 3, "5")],
        ),
        # 5 | work status | 1: the work status still goes with the PR-2, after it
        (
            [_row(1, "5", _THERAPY), _row(2, "100", _WORK_STATUS), _row(3)],
            [(1, 1, "5"), (2, 3, "1")],
        ),
        # 5 | RFA | 1: the same for an RFA
        (
            [_row(1, "5", _THERAPY), _row(2, "10", _RFA), _row(3)],
            [(1, 1, "5"), (2, 3, "1")],
        ),
        # 5 | work status | 5: one therapy visit, nothing to keep apart
        (
            [_row(1, "5", _THERAPY), _row(2, "100", _WORK_STATUS), _row(3, "5", _THERAPY)],
            [(1, 3, "5")],
        ),
    ],
    ids=["1|ws|5", "5|ws|1", "5|rfa|1", "5|ws|5"],
)
def test_a_run_never_joins_a_pr2_and_a_therapy_visit(rows, expected):
    """GUARD (the bridge): a category 1 visit and a category 5 visit never end up in one row
    through a work status or an RFA between them; that row goes with the category 1 side, the
    export fold's order. On the live copy reviewers kept the therapy note apart in 5 of 10 such
    runs."""
    out, _ = visit_merge.auto_merge_visit_pieces(rows)
    assert _spans(out) == expected


@pytest.mark.parametrize(
    "rows",
    [
        [_row(1), _row(2, date="02/05/2026")],  # two dates
        [_row(1), _row(2, date="-")],  # a date missing
        [_row(1), _row(2, "3", "MRI OF THE LUMBAR SPINE")],  # a diagnostic study
        [_row(1), _row(2, "5", _THERAPY)],  # a PR-2 and a therapy note
        [_row(1, "2", "PR-4 REPORT"), _row(2, "2", "PR-4 REPORT")],  # a PR-4: hints only
        [_row(1, "9", "DEPOSITION"), _row(2, "100", "ERRATA SHEET")],  # a deposition tail: a hint
    ],
    ids=["two dates", "no date", "study", "1+5", "2+2", "deposition tail"],
)
def test_nothing_else_is_merged(rows):
    """GUARD: only the visit merge's candidates as they are today - the PR-4 and deposition pairs
    stay hints."""
    out, stats = visit_merge.auto_merge_visit_pieces(rows)
    assert _spans(out) == _spans(rows)
    assert stats == {"runs": 0, "merged": 0}


def test_the_pieces_of_each_merged_row_are_keyed_by_its_pages():
    """DEMONSTRATES what "Split back" restores: every run, under the merged row's pages."""
    rows = [_row(1), _row(2), _row(3, "3", "X-RAY"), _row(4, "5", _THERAPY), _row(5, "5", _THERAPY)]
    pieces = visit_merge.auto_merge_pieces(rows)
    assert sorted(pieces) == [(1, 2), (4, 5)]
    assert [(p["start"], p["end"]) for p in pieces[(4, 5)]] == [(4, 4), (5, 5)]


# --- the setting -----------------------------------------------------------------------------


def test_the_setting_is_off_by_default():
    assert get_settings().__class__.model_fields["auto_merge_visit_pieces"].default is False


def test_the_two_visit_merges_refuse_to_be_on_together(monkeypatch):
    """GUARD: the model-checked merge would ask about pieces the automatic one merges anyway."""
    monkeypatch.setenv("AUTO_MERGE_VISIT_PIECES", "true")
    monkeypatch.setenv("MERGE_VISIT_PIECES", "true")
    with pytest.raises(ValidationError, match="AUTO_MERGE_VISIT_PIECES and MERGE_VISIT_PIECES"):
        Settings()  # type: ignore[call-arg]
    monkeypatch.setenv("MERGE_VISIT_PIECES", "false")
    assert Settings().auto_merge_visit_pieces is True  # type: ignore[call-arg]


# --- where it is applied: the reviewer's copy only ---------------------------------------------


def _make_document(page_count=4) -> str:
    with get_sessionmaker()() as session:
        user = User(
            email=unique_test_email(),
            name="Auto merge",
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


_DRAFT = [_row(1), _row(2), _row(3, "3", "X-RAY"), _row(4, "100", "FAX COVER", date="-")]


def _segment(monkeypatch, enabled):
    import app.services.page_text as page_text_mod
    import app.services.segment_engine as se
    from app.worker.tasks import segment_document

    monkeypatch.setattr(page_text_mod, "populate_document", lambda *a, **k: 0)
    monkeypatch.setattr(
        se,
        "run_segmentation",
        lambda pdf_path, total_pages, progress=None, page_text_fn=None: [dict(r) for r in _DRAFT],
    )
    monkeypatch.setattr(get_settings(), "auto_merge_visit_pieces", enabled)
    doc_id = _make_document()
    with get_sessionmaker()() as session:
        job_id = jobs.create_job(session, doc_id, "segment", model="m", prompt_version="1").id
    segment_document(job_id)
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "done"
        review = session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
        ).all()
        segment = session.scalars(
            select(SegmentRow).where(SegmentRow.job_id == job_id).order_by(SegmentRow.idx)
        ).all()
        return (
            doc_id,
            [(r.start, r.end, r.category, r.include) for r in review],
            [(r.start, r.end) for r in segment],
        )


def test_the_reviewers_copy_is_merged_and_the_machines_keeps_every_piece(monkeypatch):
    """DEMONSTRATES: review_rows get the merged row, segment_rows keep the pieces - so every
    measurement of the draft stays honest and "Split back" has something to restore."""
    _doc, review, segment = _segment(monkeypatch, enabled=True)
    assert [r[:3] for r in review] == [(1, 2, "1"), (3, 3, "3"), (4, 4, "100")]
    assert segment == [(1, 1), (2, 2), (3, 3), (4, 4)]
    # include still follows the category: the visit ticked, General not.
    assert [r[3] for r in review] == [True, True, False]


def test_with_the_setting_off_both_copies_are_the_draft(monkeypatch):
    """GUARD: off - the default - is today's draft."""
    _doc, review, segment = _segment(monkeypatch, enabled=False)
    assert [r[:2] for r in review] == segment == [(1, 1), (2, 2), (3, 3), (4, 4)]


# --- what the editor is sent -----------------------------------------------------------------


@pytest.fixture
async def authed(client, seeded_user):
    email, password = seeded_user
    resp = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 204
    with get_sessionmaker()() as session:
        return client, session.scalar(select(User.id).where(User.email == email))


def _seed_draft(user_id, review_spans):
    """A document of ``user_id`` with a done segment job holding `_DRAFT`'s pieces, and review
    rows over ``review_spans``."""
    doc_id = _make_document()
    with get_sessionmaker()() as session:
        session.get(Document, doc_id).user_id = user_id
        job = Job(document_id=doc_id, kind="segment", state="done", model="m", prompt_version="v")
        session.add(job)
        session.flush()
        for idx, row in enumerate(_DRAFT):
            fields = {k: row[k] for k in ("start", "end", "category", "title", "date", "method")}
            session.add(SegmentRow(job_id=job.id, idx=idx, **fields))
        for idx, (start, end, category) in enumerate(review_spans):
            session.add(
                ReviewRow(document_id=doc_id, idx=idx, start=start, end=end, category=category)
            )
        session.commit()
    return doc_id


async def test_a_merged_row_carries_its_pieces_for_split_back(authed, monkeypatch):
    """DEMONSTRATES: the editor gets the pieces of a row the automatic merge made."""
    client, user_id = authed
    monkeypatch.setattr(get_settings(), "auto_merge_visit_pieces", True)
    doc_id = _seed_draft(user_id, [(1, 2, "1"), (3, 3, "3"), (4, 4, "100")])
    rows = (await client.get(f"/api/documents/{doc_id}")).json()["rows"]
    pieces = rows[0]["merged_pieces"]
    assert [(p["start"], p["end"], p["category"]) for p in pieces] == [(1, 1, "1"), (2, 2, "1")]
    assert all(p["include"] is True and p["method"] == "rules" for p in pieces)
    assert "merged_pieces" not in rows[1] and "merged_pieces" not in rows[2]


async def test_a_row_the_reviewer_re_spanned_offers_no_split_back(authed, monkeypatch):
    """GUARD: once the reviewer changes a merged row's pages, there is nothing to split back."""
    client, user_id = authed
    monkeypatch.setattr(get_settings(), "auto_merge_visit_pieces", True)
    doc_id = _seed_draft(user_id, [(1, 3, "1"), (4, 4, "100")])
    rows = (await client.get(f"/api/documents/{doc_id}")).json()["rows"]
    assert not any("merged_pieces" in row for row in rows)


async def test_no_pieces_are_sent_with_the_setting_off(authed, monkeypatch):
    """GUARD: off, a row the reviewer merged by hand is just a row."""
    client, user_id = authed
    monkeypatch.setattr(get_settings(), "auto_merge_visit_pieces", False)
    doc_id = _seed_draft(user_id, [(1, 2, "1"), (3, 3, "3"), (4, 4, "100")])
    rows = (await client.get(f"/api/documents/{doc_id}")).json()["rows"]
    assert not any("merged_pieces" in row for row in rows)
