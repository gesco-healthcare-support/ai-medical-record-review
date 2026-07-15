"""P3b integration tests for the /api/documents router, driving the ASGI app on docker Postgres.

Covers ownership/IDOR (404 on a non-owner), upload/list/get/delete, row validation + persistence,
summaries + export, category bundles, and the sync-AI routes (resummarize / bundle-summarize) with
the Vertex boundary MOCKED - proving both the happy docx path and the friendly PipelineError
response, without a live model call. Uploads are redirected to a tmp dir so no files leak.
"""

import io

import pytest
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.errors import OcrUnavailableError
from app.models import Job, Summary, User
from app.services.seed_catalog import constants_categories
from tests.conftest import unique_test_email

_VALID_CATEGORY = constants_categories()[0]["id"]
_OTHER_CATEGORY = next(c["id"] for c in constants_categories() if c["id"] != _VALID_CATEGORY)


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    # Redirect PHI-at-rest uploads to a per-test tmp dir (pytest cleans it up).
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture
async def authed(client, seeded_user):
    """The shared client, logged in as the seeded dev-salt user; yields (client, user_id)."""
    email, password = seeded_user
    resp = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 204
    with get_sessionmaker()() as session:
        user_id = session.scalar(select(User.id).where(User.email == email))
    return client, user_id


def _pdf_bytes(pages: int = 1) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def _upload(client, pages: int = 1) -> str:
    resp = await client.post(
        "/api/documents",
        files={"pdf": ("scan.pdf", _pdf_bytes(pages), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_documents_require_auth(client):
    # No login -> the app-level gate denies (JSON client -> 401).
    assert (await client.get("/api/documents")).status_code == 401
    assert (await client.get("/api/documents/whatever")).status_code == 401


async def test_nonexistent_document_is_404(authed):
    client, _ = authed
    assert (await client.get("/api/documents/does-not-exist")).status_code == 404
    assert (await client.delete("/api/documents/does-not-exist")).status_code == 404
    assert (await client.get("/api/documents/does-not-exist/pdf")).status_code == 404


async def test_idor_other_users_document_is_404(authed, client):
    client, _ = authed
    doc_id = await _upload(client, pages=1)

    # A second user cannot see the first user's document -> 404 (never 403).
    email_b, password_b = unique_test_email(), "Str0ng#pw1"
    with get_sessionmaker()() as session:
        session.add(
            User(
                email=email_b, name="B", password=MrrPasswordHelper().hash(password_b), active=True
            )
        )
        session.commit()
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", data={"username": email_b, "password": password_b})
    assert (await client.get(f"/api/documents/{doc_id}")).status_code == 404


async def test_upload_list_get_status_delete(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=2)

    listing = await client.get("/api/documents")
    assert any(d["id"] == doc_id and d["page_count"] == 2 for d in listing.json())

    got = await client.get(f"/api/documents/{doc_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["id"] == doc_id and body["rows"] == [] and "categories" in body

    status = await client.get(f"/api/documents/{doc_id}/status")
    assert status.json() == {"status": "uploaded", "job": None}

    assert (await client.delete(f"/api/documents/{doc_id}")).status_code == 200
    assert (await client.get(f"/api/documents/{doc_id}")).status_code == 404


async def test_upload_rejects_non_pdf(authed):
    client, _ = authed
    resp = await client.post(
        "/api/documents", files={"pdf": ("x.pdf", b"not a pdf", "application/pdf")}
    )
    assert resp.status_code == 400


async def test_rows_put_validation_and_persistence(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=3)

    ok = await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 2, "category": _VALID_CATEGORY}]},
    )
    assert ok.status_code == 200 and ok.json()["count"] == 1

    bad_range = await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 5, "end": 2, "category": _VALID_CATEGORY}]},
    )
    assert bad_range.status_code == 400

    got = await client.get(f"/api/documents/{doc_id}")
    assert len(got.json()["rows"]) == 1  # the valid PUT persisted; the bad one did not replace it


async def test_summaries_empty_and_export_conflict(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    assert (await client.get(f"/api/documents/{doc_id}/summaries")).json() == []
    export = await client.post(
        f"/api/documents/{doc_id}/export", json={"patientName": "Synthetic Patient"}
    )
    assert export.status_code == 409  # no summaries to export yet


async def test_bundle_pdf_and_category_errors(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 1, "category": _VALID_CATEGORY}]},
    )

    ok = await client.post(
        f"/api/documents/{doc_id}/bundle/pdf",
        json={"categories": [_VALID_CATEGORY], "label": "Diagnostic & Operative"},
    )
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "application/pdf"

    empty = await client.post(f"/api/documents/{doc_id}/bundle/pdf", json={"categories": []})
    assert empty.status_code == 400  # non-empty list required

    unmatched = await client.post(
        f"/api/documents/{doc_id}/bundle/pdf", json={"categories": [_OTHER_CATEGORY]}
    )
    assert unmatched.status_code == 409  # nothing in this record matches


async def test_bundle_summarize_ocr_unavailable_returns_friendly_503(authed, monkeypatch):
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 1, "category": _VALID_CATEGORY}]},
    )

    import app.services.summarize_engine as se

    def boom(*_args, **_kwargs):
        raise OcrUnavailableError("no tesseract")

    monkeypatch.setattr(se, "summarize_row", boom)
    resp = await client.post(
        f"/api/documents/{doc_id}/bundle/summarize", json={"categories": [_VALID_CATEGORY]}
    )
    assert resp.status_code == 503
    assert "OCR" in resp.json()["error"]  # friendly message, never the raw vendor error


async def test_bundle_summarize_happy_path_returns_docx(authed, monkeypatch):
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 1, "category": _VALID_CATEGORY}]},
    )

    import app.services.summarize_engine as se

    def fake(_pdf_path, row, _model=None, prompt=None):
        return {
            "summaryDate": row.get("date", "-"),
            "summaryTitle": "T (Pages 1-1)",
            "summaryText": "body",
            "manualCheck": "",
            "sourceText": "x",
        }

    monkeypatch.setattr(se, "summarize_row", fake)
    resp = await client.post(
        f"/api/documents/{doc_id}/bundle/summarize",
        json={"categories": [_VALID_CATEGORY], "patientName": "Synthetic Patient"},
    )
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers["content-type"]


async def test_resummarize_mocked_happy_path(authed, monkeypatch):
    client, _ = authed
    doc_id = await _upload(client, pages=1)

    # A resummarize target needs an existing Summary (and its owning Job).
    with get_sessionmaker()() as session:
        job = Job(document_id=doc_id, kind="summarize", state="done", model="m", prompt_version="1")
        session.add(job)
        session.flush()
        session.add(
            Summary(
                document_id=doc_id,
                job_id=job.id,
                idx=0,
                title="old title",
                text="old text",
                row_start=1,
                row_end=1,
                row_category=_VALID_CATEGORY,
            )
        )
        session.commit()

    import app.api.documents as documents_module

    def fake(_pdf_path, _row, _model=None, prompt=None):
        return {
            "summaryTitle": "New Title (Pages 1-1)",
            "summaryDate": "-",
            "summaryText": "new body",
            "manualCheck": "",
            "sourceText": "x",
        }

    monkeypatch.setattr(documents_module, "summarize_row", fake)
    resp = await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")
    assert resp.status_code == 200
    assert resp.json()["summaryTitle"].startswith("New Title")
