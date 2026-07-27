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
from app.models import Document, User
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
