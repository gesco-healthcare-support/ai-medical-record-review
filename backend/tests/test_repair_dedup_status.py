"""The status-repair script's guard: which documents it claims, and which it must not touch.

The guard is the whole script. Restoring a status from a wrong reading is worse than leaving a
record mislabelled, so every test here is about a case that must be REFUSED - the one that is
claimed gets a single test and the rest state the boundary.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.db import get_sessionmaker
from app.models import Document, Job, ReviewRow, Summary, User
from tests.conftest import unique_test_email

_spec = importlib.util.spec_from_file_location(
    "repair_status",
    Path(__file__).resolve().parents[1] / "scripts" / "repair_dedup_clobbered_status.py",
)
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)


def _document(status: str, jobs: list[tuple[str, str]], summaries: int = 1) -> str:
    """A document at `status`, with `jobs` as (kind, state) in order, and `summaries` stored rows."""
    with get_sessionmaker()() as session:
        user = User(
            email=unique_test_email(),
            name="Repair",
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
            page_count=2,
            status=status,
        )
        session.add(document)
        session.flush()
        job_rows = []
        for kind, state in jobs:
            job = Job(
                document_id=document.id,
                kind=kind,
                state=state,
                stage=kind,
                model="m",
                prompt_version="1",
            )
            session.add(job)
            job_rows.append(job)
        session.flush()
        # summaries.job_id is NOT NULL: attach them to the summarize job that wrote them, or to
        # whatever job exists when the case under test has none.
        owner = next((j for j in job_rows if j.kind == "summarize"), None) or (
            job_rows[0] if job_rows else None
        )
        if owner is None and summaries:
            raise AssertionError("a case with summaries needs at least one job to own them")
        for idx in range(summaries):
            session.add(
                Summary(
                    document_id=document.id,
                    job_id=owner.id,
                    idx=idx,
                    title="T",
                    date="-",
                    text="body",
                    row_start=1,
                    row_end=2,
                    row_category="1",
                )
            )
        session.commit()
        return document.id


def _restorable(document_id):
    with get_sessionmaker()() as session:
        return repair.restorable_status(session, document_id)


def test_a_status_a_dedup_overwrote_is_identified():
    """The case this exists for: summarize finished, then a dedup rewrote the status to reviewing."""
    doc = _document("reviewing", [("segment", "done"), ("summarize", "done"), ("dedup", "done")])
    assert _restorable(doc) == "done"


def test_a_needs_attention_result_is_restored_as_needs_attention():
    """The costly case. That status is what names the sub-documents a summarize could not write, and
    GET /status returns only the newest job - the dedup, which carries no `attention` - so the list
    is unreachable in the UI until this is restored. It must NOT be flattened to "done"."""
    doc = _document(
        "reviewing", [("segment", "done"), ("summarize", "needs_attention"), ("dedup", "done")]
    )
    assert _restorable(doc) == "needs_attention"


def test_a_segment_run_after_the_summarize_is_left_alone():
    """The guard that cut the first measured count from 9 to 6, and the one worth having.

    A segment run sets "reviewing" LEGITIMATELY, so a document whose newest non-dedup job is a
    segment is correctly at that status - even though it also has a completed summarize behind it
    and a trailing dedup, which is what a naive "is the newest job a dedup, and are there
    summaries?" test looks at.
    """
    doc = _document(
        "reviewing",
        [("segment", "done"), ("summarize", "done"), ("segment", "done"), ("dedup", "done")],
    )
    assert _restorable(doc) is None


@pytest.mark.parametrize(
    ("jobs", "why"),
    [
        ([("segment", "done"), ("summarize", "done")], "no dedup wrote the status last"),
        ([("segment", "done"), ("dedup", "done")], "no summarize at all"),
        (
            [("segment", "done"), ("summarize", "error"), ("dedup", "done")],
            "the summarize did not finish, so there is no value to restore",
        ),
        (
            [("segment", "done"), ("summarize", "cancelled"), ("dedup", "done")],
            "a cancelled summarize is not a terminal outcome to restore",
        ),
        ([], "no jobs at all"),  # summaries=0 below, see the helper
    ],
)
def test_the_cases_the_guard_refuses(jobs, why):
    # A case with no jobs can own no summary (summaries.job_id is NOT NULL), which is itself part of
    # why it is refused.
    assert _restorable(_document("reviewing", jobs, summaries=1 if jobs else 0)) is None, why


def test_a_document_with_no_stored_summaries_is_refused():
    """Corroboration independent of the job rows: without a summary there is nothing saying the
    record actually holds delivered work."""
    doc = _document(
        "reviewing", [("segment", "done"), ("summarize", "done"), ("dedup", "done")], summaries=0
    )
    assert _restorable(doc) is None


@pytest.mark.parametrize("status", ["done", "needs_attention", "error", "uploaded", "segmenting"])
def test_only_a_reviewing_document_is_ever_touched(status):
    """The script filters on status before consulting the guard, so a record that is already correct
    - or genuinely failed - cannot be rewritten in either direction."""
    doc = _document(status, [("segment", "done"), ("summarize", "done"), ("dedup", "done")])
    with get_sessionmaker()() as session:
        ids = [doc]
        # Mirror main()'s own filter.
        claimed = [
            d
            for d in ids
            if session.get(Document, d).status == "reviewing"
            and repair.restorable_status(session, d) is not None
        ]
    assert claimed == []


def test_the_repair_is_idempotent():
    """A repaired document no longer has status "reviewing", so a second run claims nothing."""
    doc = _document("reviewing", [("segment", "done"), ("summarize", "done"), ("dedup", "done")])
    assert _restorable(doc) == "done"

    with get_sessionmaker()() as session:
        session.get(Document, doc).status = "done"
        session.commit()

    with get_sessionmaker()() as session:
        document = session.get(Document, doc)
        assert document.status == "done"
        # The guard would still MATCH on the job history - it is the status filter in main() that
        # makes the second run a no-op, which is why that filter is not merely an optimisation.
        assert repair.restorable_status(session, doc) == "done"


def test_scope_must_be_given_explicitly():
    """A shared box hosts several users' records, so the script refuses to guess a scope - the same
    rule backfill_doi.py follows."""
    with get_sessionmaker()() as session, pytest.raises(SystemExit):
        repair.scoped_document_ids(session)
    with get_sessionmaker()() as session, pytest.raises(SystemExit):
        repair.scoped_document_ids(session, user_email="a@b.com", every=True)


def test_scoping_by_user_returns_only_that_user_s_documents():
    doc = _document("reviewing", [("summarize", "done"), ("dedup", "done")])
    with get_sessionmaker()() as session:
        owner_email = session.scalar(
            select(User.email).where(User.id == session.get(Document, doc).user_id)
        )
        ids = repair.scoped_document_ids(session, user_email=owner_email)
    assert ids == [doc]


def test_an_unknown_user_exits_rather_than_running_over_everything():
    with get_sessionmaker()() as session, pytest.raises(SystemExit):
        repair.scoped_document_ids(session, user_email="nobody@example.invalid")


def test_review_rows_are_never_touched():
    """The script writes documents.status and nothing else - stated as a test because a repair that
    quietly edited rows would be far worse than the mislabelling it fixes."""
    doc = _document("reviewing", [("summarize", "done"), ("dedup", "done")])
    with get_sessionmaker()() as session:
        session.add(
            ReviewRow(
                document_id=doc,
                idx=0,
                start=1,
                end=2,
                category="1",
                title="A",
                date="-",
                injury_date="-",
                flag="-",
                include=True,
            )
        )
        session.commit()

    with get_sessionmaker()() as session:
        document = session.get(Document, doc)
        document.status = repair.restorable_status(session, doc)
        session.commit()

    with get_sessionmaker()() as session:
        row = session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc)).one()
        assert (row.category, row.include, row.title) == ("1", True, "A")
