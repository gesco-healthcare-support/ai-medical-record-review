"""Admin opt-in re-processing: re-summarize a document with the current prompts, replacing
the prior summaries, and stamp the catalog revision on the job. Engines stubbed; synthetic
data only."""

import io
import time
from types import SimpleNamespace

import pytest
from flask_security import hash_password

from mrr_ai import catalog
from mrr_ai.extensions import db

_ROWS = [
    {
        "start": 1,
        "end": 4,
        "category": "1",
        "title": "A",
        "date": "-",
        "injury_date": "-",
        "flag": "-",
        "include": True,
    },
]


def _login(client, user):
    return client.post(
        "/login", data={"email": user.email, "password": user.password}, follow_redirects=True
    )


@pytest.fixture
def admin_client(app):
    with app.app_context():
        datastore = app.extensions["security"].datastore
        record = datastore.create_user(
            email="admin@example.com", password=hash_password("password123")
        )
        record.is_admin = True
        db.session.commit()
        admin = SimpleNamespace(email="admin@example.com", password="password123")
    test_client = app.test_client()
    _login(test_client, admin)
    return test_client


def _fake_summarize(pdf_path, row, model=None, prompt=None):
    return {
        "summaryTitle": f"S{row['start']}",
        "summaryDate": row["date"],
        "summaryText": "text",
        "manualCheck": False,
    }


def _upload(client, pdf_bytes):
    resp = client.post(
        "/api/documents",
        data={"pdf": (io.BytesIO(pdf_bytes(10)), "synthetic.pdf")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    return resp.get_json()["id"]


def _wait_done(client, document_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/documents/{document_id}/status").get_json()
        if payload["status"] == "done":
            return True
        if payload["status"] == "error":
            raise AssertionError(f"job failed: {payload['job']}")
        time.sleep(0.02)
    return False


def test_reprocess_reruns_and_stamps_catalog_revision(admin_client, app, pdf_bytes, monkeypatch):
    monkeypatch.setattr("mrr_ai.services.summarize_engine.summarize_row", _fake_summarize)
    document_id = _upload(admin_client, pdf_bytes)
    # First summarize stores the rows and produces summaries.
    admin_client.post(f"/api/documents/{document_id}/summarize/start", json={"rows": _ROWS})
    assert _wait_done(admin_client, document_id)

    # An admin edits a prompt (bumps the catalog revision), then re-processes.
    admin_client.put("/api/admin/prompts/1", json={"text": "new prompt"})
    resp = admin_client.post(f"/api/admin/reprocess/{document_id}")
    assert resp.status_code == 200
    assert _wait_done(admin_client, document_id)

    with app.app_context():
        from mrr_ai.models import Job, Summary

        latest = (
            Job.query.filter_by(document_id=document_id, kind="summarize")
            .order_by(Job.id.desc())
            .first()
        )
        assert latest.catalog_revision == catalog.catalog_version()
        # Summaries were replaced, not appended (one included row -> exactly one summary).
        assert Summary.query.filter_by(document_id=document_id).count() == 1


def test_reprocess_unknown_document_404(admin_client):
    assert admin_client.post("/api/admin/reprocess/does-not-exist").status_code == 404


def test_reprocess_document_without_rows_400(admin_client, pdf_bytes):
    document_id = _upload(admin_client, pdf_bytes)  # uploaded but never reviewed
    assert admin_client.post(f"/api/admin/reprocess/{document_id}").status_code == 400
