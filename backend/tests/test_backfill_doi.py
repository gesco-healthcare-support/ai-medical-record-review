"""The DOI backfill's SCOPE resolution (scripts/backfill_doi.py).

This script rewrites stored summaries and re-sends pages to the model, and the boxes it runs on host
more than one account's records - so "which documents" must never be guessed. These tests pin that:
no scope is an error, and a scope never reaches beyond the account or record named.

The script lives outside the app package (dev tooling), so it is loaded by path.
"""

import importlib.util
import os
import uuid

import pytest

from app.auth.password import MrrPasswordHelper
from app.db import get_sessionmaker
from app.models import Document, Job, Summary, User
from tests.conftest import unique_test_email

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "backfill_doi.py"
)
_spec = importlib.util.spec_from_file_location("backfill_doi", _PATH)
backfill_doi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill_doi)


def _user_with_document(session) -> tuple[int, str]:
    user = User(
        email=unique_test_email(),
        name="Backfill",
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
    )
    session.add(document)
    session.commit()
    return user.email, document.id


def test_a_run_without_a_scope_is_refused():
    with get_sessionmaker()() as session, pytest.raises(SystemExit) as exit_info:
        backfill_doi.scoped_document_ids(session)
    assert "scope" in str(exit_info.value)


def test_more_than_one_scope_is_refused():
    with get_sessionmaker()() as session, pytest.raises(SystemExit):
        backfill_doi.scoped_document_ids(session, user_email="a@b.c", every=True)


def test_user_scope_covers_only_that_account():
    with get_sessionmaker()() as session:
        email, doc_id = _user_with_document(session)
        _, other_doc_id = _user_with_document(session)  # a second account on the same box

        ids = backfill_doi.scoped_document_ids(
            session, user_email=email.upper()
        )  # case-insensitive
        assert ids == [doc_id]
        assert other_doc_id not in ids


def test_document_scope_is_validated():
    with get_sessionmaker()() as session:
        _, doc_id = _user_with_document(session)
        assert backfill_doi.scoped_document_ids(session, document_ids=[doc_id]) == [doc_id]

        with pytest.raises(SystemExit) as exit_info:
            backfill_doi.scoped_document_ids(session, document_ids=[doc_id, "not-a-document"])
        assert "not-a-document" in str(exit_info.value)


def test_unknown_email_is_refused():
    with get_sessionmaker()() as session, pytest.raises(SystemExit) as exit_info:
        backfill_doi.scoped_document_ids(session, user_email="nobody@example.invalid")
    assert "nobody@example.invalid" in str(exit_info.value)


_BODY = "**DOI**:01/01/2000, Lumbar strain noted."


def _summary_for(session, doc_id, **over):
    fields = dict(
        document_id=doc_id,
        job_id=Job(
            document_id=doc_id, kind="summarize", state="done", model="m", prompt_version="1"
        ),
        idx=0,
        title="Work Status Report (Pages 1-2)",
        date="-",
        text=_BODY,
        row_start=1,
        row_end=2,
        row_category="1",
    )
    fields.update(over)
    job = fields.pop("job_id")
    session.add(job)
    session.flush()
    summary = Summary(job_id=job.id, **fields)
    session.add(summary)
    session.commit()
    return summary.id


def _texts(session, summary_id):
    summary = session.get(Summary, summary_id)
    session.refresh(summary)
    return (summary.text, summary.verified_text, summary.edited_text)


def test_dry_run_writes_nothing(monkeypatch):
    with get_sessionmaker()() as session:
        _, doc_id = _user_with_document(session)
        summary_id = _summary_for(session, doc_id)
        monkeypatch.setattr(backfill_doi, "extract_injury_date", lambda *a, **k: "-")

        changed, skipped = backfill_doi.run(session, [doc_id], dry_run=True)
        assert (changed, skipped) == (1, 0)  # it WOULD strip the propagated date
    with get_sessionmaker()() as check:
        assert _texts(check, summary_id) == (_BODY, None, None)  # but wrote nothing


def test_an_unreadable_document_never_loses_its_stored_date(monkeypatch):
    """ "-" means "this document states no injury date". A read failure must not say that."""

    def boom(*args, **kwargs):
        raise RuntimeError("Reauthentication is needed")

    with get_sessionmaker()() as session:
        _, doc_id = _user_with_document(session)
        summary_id = _summary_for(session, doc_id)
        monkeypatch.setattr(backfill_doi, "extract_injury_date", boom)

        with pytest.raises(SystemExit) as exit_info:
            backfill_doi.run(session, [doc_id])
        assert "every extraction failed" in str(exit_info.value)
    with get_sessionmaker()() as check:
        assert _texts(check, summary_id) == (_BODY, None, None)


def test_a_working_run_rewrites_once_and_is_idempotent(monkeypatch):
    with get_sessionmaker()() as session:
        _, doc_id = _user_with_document(session)
        summary_id = _summary_for(session, doc_id)
        monkeypatch.setattr(backfill_doi, "extract_injury_date", lambda *a, **k: "05/08/2022")

        assert backfill_doi.run(session, [doc_id]) == (1, 0)
        assert backfill_doi.run(session, [doc_id]) == (0, 0)
    with get_sessionmaker()() as check:
        text, _, _ = _texts(check, summary_id)
        assert text == "**DOI**:05/08/2022, Lumbar strain noted."


def test_one_unreadable_summary_does_not_block_the_others(monkeypatch):
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("quota exceeded")
        return "05/08/2022"

    with get_sessionmaker()() as session:
        _, doc_id = _user_with_document(session)
        first = _summary_for(session, doc_id)
        second = _summary_for(session, doc_id, idx=1, row_start=3, row_end=4)
        monkeypatch.setattr(backfill_doi, "extract_injury_date", flaky)

        assert backfill_doi.run(session, [doc_id]) == (1, 1)
    with get_sessionmaker()() as check:
        assert _texts(check, first)[0] == _BODY  # untouched
        assert _texts(check, second)[0] == "**DOI**:05/08/2022, Lumbar strain noted."
