"""T5: document-scoped API - ownership (IDOR), uploads, rows, jobs, summaries, export.

Engines are stubbed (no Gemini/network); PDFs are tiny synthetic blanks. The wait
helpers poll the real /status endpoint, so job wiring is exercised end to end.
"""

import io
import threading
import time

import pytest


def _upload(client, pdf_bytes, pages=10, name="synthetic-case.pdf"):
    return client.post(
        "/api/documents",
        data={"pdf": (io.BytesIO(pdf_bytes(pages)), name)},
        content_type="multipart/form-data",
    )


def _wait_status(client, document_id, wanted, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/documents/{document_id}/status").get_json()
        if payload["status"] == wanted:
            return True
        if payload["status"] == "error":
            raise AssertionError(f"job failed: {payload['job']}")
        time.sleep(0.02)
    return False


_ROWS = [
    {
        "start": 1,
        "end": 5,
        "category": "1",
        "title": "DOC A",
        "date": "01/15/2024",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
    },
    {
        "start": 6,
        "end": 10,
        "category": "3",
        "title": "DOC B",
        "date": "02/20/2024",
        "injury_date": "-",
        "flag": "x",
        "suggest_merge": True,
    },
]


@pytest.fixture
def other_client(app):
    """A SECOND authenticated user - the adversary in the ownership tests."""
    from flask_security import hash_password

    from mrr_ai.extensions import db

    with app.app_context():
        datastore = app.extensions["security"].datastore
        datastore.create_user(email="other@example.com", password=hash_password("password456"))
        db.session.commit()
    test_client = app.test_client()
    test_client.post("/login", data={"email": "other@example.com", "password": "password456"})
    return test_client


def test_upload_stores_under_user_dir_and_flags_duplicates(app, client, user, pdf_bytes):
    first = _upload(client, pdf_bytes)
    assert first.status_code == 201
    body = first.get_json()
    assert body["page_count"] == 10
    assert body["sha256_duplicate"] is False

    from mrr_ai.extensions import db
    from mrr_ai.models import Document

    with app.app_context():
        document = db.session.get(Document, body["id"])
        assert f"{user.id}" in document.stored_path
        assert document.stored_path.endswith(body["id"] + ".pdf")

    again = _upload(client, pdf_bytes)  # identical bytes -> warn, never block
    assert again.status_code == 201
    assert again.get_json()["sha256_duplicate"] is True


def test_upload_rejects_unreadable_pdf(client):
    response = client.post(
        "/api/documents",
        data={"pdf": (io.BytesIO(b"not a pdf at all"), "junk.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_other_user_gets_404_everywhere(client, other_client, pdf_bytes):
    document_id = _upload(client, pdf_bytes).get_json()["id"]

    assert other_client.get("/api/documents").get_json() == []  # not in their list
    probes = [
        ("GET", f"/api/documents/{document_id}"),
        ("GET", f"/api/documents/{document_id}/pdf"),
        ("GET", f"/api/documents/{document_id}/status"),
        ("GET", f"/api/documents/{document_id}/summaries"),
        ("PUT", f"/api/documents/{document_id}/rows"),
        ("POST", f"/api/documents/{document_id}/segment/start"),
        ("POST", f"/api/documents/{document_id}/summarize/start"),
        ("POST", f"/api/documents/{document_id}/export"),
        ("POST", f"/api/documents/{document_id}/summaries/0/resummarize"),
        ("POST", f"/api/documents/{document_id}/bundle/pdf"),
        ("DELETE", f"/api/documents/{document_id}"),
    ]
    for method, url in probes:
        response = other_client.open(url, method=method, json={})
        assert response.status_code == 404, (method, url)


def test_put_rows_validates_and_persists(client, pdf_bytes):
    document_id = _upload(client, pdf_bytes).get_json()["id"]

    response = client.put(
        f"/api/documents/{document_id}/rows",
        json={"rows": [_ROWS[0], dict(_ROWS[1], start=3)]},  # start 3 overlaps pages 1-5
    )
    assert response.status_code == 400
    assert "row 2" in response.get_json()["error"]

    assert client.put(f"/api/documents/{document_id}/rows", json={"rows": _ROWS}).status_code == 200
    detail = client.get(f"/api/documents/{document_id}").get_json()
    assert [row["start"] for row in detail["rows"]] == [1, 6]
    assert detail["rows"][1]["suggest_merge"] is True


def test_put_rows_blocked_while_job_active(app, client, pdf_bytes):
    from mrr_ai.services import job_queue

    document_id = _upload(client, pdf_bytes).get_json()["id"]
    release = threading.Event()

    with app.app_context():
        job_queue.submit(
            document_id,
            "segment",
            lambda report: release.wait(10),
            model="m",
            prompt_version="2",
        )
    try:
        response = client.put(f"/api/documents/{document_id}/rows", json={"rows": _ROWS})
        assert response.status_code == 409
    finally:
        release.set()


def test_segment_persists_raw_and_review_rows(app, client, pdf_bytes, monkeypatch):
    def fake_segmentation(pdf_path, total_pages, progress=None):
        progress("segmenting", 1, 1)
        return [dict(row) for row in _ROWS]

    monkeypatch.setattr("mrr_ai.services.segment_engine.run_segmentation", fake_segmentation)
    document_id = _upload(client, pdf_bytes).get_json()["id"]
    assert client.post(f"/api/documents/{document_id}/segment/start").status_code == 200
    assert _wait_status(client, document_id, "reviewing")

    detail = client.get(f"/api/documents/{document_id}").get_json()
    assert [row["start"] for row in detail["rows"]] == [1, 6]
    assert detail["rows"][1]["suggest_merge"] is True

    from mrr_ai.models import Job, SegmentRow
    from mrr_ai.services.gemini import PROMPT_VERSION

    with app.app_context():
        job = Job.query.filter_by(document_id=document_id).one()
        assert job.state == "done"
        assert job.prompt_version == PROMPT_VERSION
        assert SegmentRow.query.filter_by(job_id=job.id).count() == 2


def test_summarize_then_export_roundtrip(app, client, pdf_bytes, monkeypatch):
    def fake_summarize(pdf_path, row, model=None, prompt=None):
        return {
            "summaryTitle": f"SUMMARY {row['start']}",
            "summaryDate": row["date"],
            "summaryText": "synthetic summary text",
            "manualCheck": row["flag"] == "x",
        }

    monkeypatch.setattr("mrr_ai.services.summarize_engine.summarize_row", fake_summarize)
    document_id = _upload(client, pdf_bytes).get_json()["id"]

    no_rows = client.post(f"/api/documents/{document_id}/summarize/start", json={})
    assert no_rows.status_code == 400  # nothing to summarize yet

    assert (
        client.post(
            f"/api/documents/{document_id}/summarize/start", json={"rows": _ROWS}
        ).status_code
        == 200
    )
    assert _wait_status(client, document_id, "done")

    summaries = client.get(f"/api/documents/{document_id}/summaries").get_json()
    assert [item["summaryTitle"] for item in summaries] == ["SUMMARY 1", "SUMMARY 6"]
    assert summaries[1]["manualCheck"] is True
    assert summaries[0]["row"] == {"start": 1, "end": 5, "category": "1"}

    exported = client.post(
        f"/api/documents/{document_id}/export", json={"patientName": "Jane Sample"}
    )
    assert exported.status_code == 200
    assert exported.data[:2] == b"PK"  # docx is a zip container


def test_export_without_summaries_conflicts(client, pdf_bytes):
    document_id = _upload(client, pdf_bytes).get_json()["id"]
    assert client.post(f"/api/documents/{document_id}/export", json={}).status_code == 409


def test_delete_removes_rows_and_file(app, client, pdf_bytes):
    document_id = _upload(client, pdf_bytes).get_json()["id"]
    client.put(f"/api/documents/{document_id}/rows", json={"rows": _ROWS})

    import os

    from mrr_ai.extensions import db
    from mrr_ai.models import Document, ReviewRow

    with app.app_context():
        stored_path = db.session.get(Document, document_id).stored_path
    assert os.path.exists(stored_path)

    assert client.delete(f"/api/documents/{document_id}").status_code == 200
    assert client.get(f"/api/documents/{document_id}").status_code == 404
    assert not os.path.exists(stored_path)
    with app.app_context():
        assert ReviewRow.query.count() == 0


def test_delete_blocked_while_job_active(app, client, pdf_bytes):
    from mrr_ai.services import job_queue

    document_id = _upload(client, pdf_bytes).get_json()["id"]
    release = threading.Event()
    with app.app_context():
        job_queue.submit(
            document_id,
            "segment",
            lambda report: release.wait(10),
            model="m",
            prompt_version="2",
        )
    try:
        assert client.delete(f"/api/documents/{document_id}").status_code == 409
    finally:
        release.set()


def test_audit_rows_written(app, client, pdf_bytes):
    document_id = _upload(client, pdf_bytes).get_json()["id"]
    client.get(f"/api/documents/{document_id}/pdf")

    from mrr_ai.models import AuditLog

    with app.app_context():
        actions = [entry.action for entry in AuditLog.query.order_by(AuditLog.id).all()]
    assert actions == ["upload", "view_pdf"]


# --- category bundles (Diagnostic & Operative / Depositions) ---------------------------

_BUNDLE_ROWS = [
    {
        "start": 1,
        "end": 3,
        "category": "1",
        "title": "progress",
        "date": "01/01/2024",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
    },
    {
        "start": 4,
        "end": 5,
        "category": "3",
        "title": "mri",
        "date": "01/02/2024",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
    },
    {
        "start": 6,
        "end": 6,
        "category": "8",
        "title": "operative",
        "date": "01/03/2024",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
    },
    {
        "start": 7,
        "end": 9,
        "category": "9",
        "title": "depo",
        "date": "01/04/2024",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
    },
    {
        "start": 10,
        "end": 10,
        "category": "13",
        "title": "qme",
        "date": "01/05/2024",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
    },
]


def _seed_bundle_doc(client, pdf_bytes):
    document_id = _upload(client, pdf_bytes, pages=10).get_json()["id"]
    assert (
        client.put(f"/api/documents/{document_id}/rows", json={"rows": _BUNDLE_ROWS}).status_code
        == 200
    )
    return document_id


def test_bundle_pdf_combines_matched_category_pages(client, pdf_bytes):
    from pypdf import PdfReader

    document_id = _seed_bundle_doc(client, pdf_bytes)
    resp = client.post(f"/api/documents/{document_id}/bundle/pdf", json={"categories": [3, 8]})
    assert resp.status_code == 200
    assert resp.data[:4] == b"%PDF"
    # cat 3 -> pages 4,5 ; cat 8 -> page 6  => 3 pages, diagnostic+operative only.
    assert len(PdfReader(io.BytesIO(resp.data)).pages) == 3


def test_bundle_pdf_no_matching_category_conflicts(client, pdf_bytes):
    document_id = _seed_bundle_doc(client, pdf_bytes)
    resp = client.post(f"/api/documents/{document_id}/bundle/pdf", json={"categories": ["2"]})
    assert resp.status_code == 409


def test_bundle_pdf_requires_a_category_list(client, pdf_bytes):
    document_id = _seed_bundle_doc(client, pdf_bytes)
    assert client.post(f"/api/documents/{document_id}/bundle/pdf", json={}).status_code == 400
    assert (
        client.post(f"/api/documents/{document_id}/bundle/pdf", json={"categories": []}).status_code
        == 400
    )


def test_bundle_summarize_builds_filtered_docx(client, pdf_bytes, monkeypatch):
    calls = []

    def fake_summarize(pdf_path, row, model=None, prompt=None):
        calls.append(row["category"])
        return {
            "summaryTitle": f"S{row['start']}",
            "summaryDate": row["date"],
            "summaryText": "body",
            "manualCheck": "",
        }

    monkeypatch.setattr("mrr_ai.services.summarize_engine.summarize_row", fake_summarize)
    document_id = _seed_bundle_doc(client, pdf_bytes)
    resp = client.post(
        f"/api/documents/{document_id}/bundle/summarize",
        json={"categories": ["9"], "label": "Depositions"},
    )
    assert resp.status_code == 200
    assert resp.data[:2] == b"PK"  # docx zip container
    assert calls == ["9"]  # only the matched category was summarized
    assert "depositions.docx" in resp.headers["Content-Disposition"]


def test_bundle_summarize_over_cap_conflicts(client, pdf_bytes, monkeypatch):
    from mrr_ai import config

    monkeypatch.setattr(config, "BUNDLE_SUMMARIZE_CAP", 1)
    document_id = _seed_bundle_doc(client, pdf_bytes)
    # cats 3+8 match 2 rows > cap of 1 -> routed to the main flow.
    resp = client.post(
        f"/api/documents/{document_id}/bundle/summarize", json={"categories": [3, 8]}
    )
    assert resp.status_code == 409


def test_bundle_rejects_non_owner(client, other_client, pdf_bytes):
    document_id = _seed_bundle_doc(client, pdf_bytes)
    assert (
        other_client.post(
            f"/api/documents/{document_id}/bundle/pdf", json={"categories": [3]}
        ).status_code
        == 404
    )


# --- inline per-summary re-run --------------------------------------------------------


def _summarized_doc(client, pdf_bytes, monkeypatch, tag_holder):
    def fake_summarize(pdf_path, row, model=None, prompt=None):
        return {
            "summaryTitle": f"{tag_holder['tag']} {row['start']}",
            "summaryDate": row["date"],
            "summaryText": f"{tag_holder['tag']} body {row['start']}",
            "manualCheck": "",
        }

    monkeypatch.setattr("mrr_ai.services.summarize_engine.summarize_row", fake_summarize)
    document_id = _upload(client, pdf_bytes).get_json()["id"]
    assert (
        client.post(
            f"/api/documents/{document_id}/summarize/start", json={"rows": _ROWS}
        ).status_code
        == 200
    )
    assert _wait_status(client, document_id, "done")
    return document_id


def test_resummarize_refreshes_one_summary_and_clears_edits(client, pdf_bytes, monkeypatch):
    holder = {"tag": "OLD"}
    document_id = _summarized_doc(client, pdf_bytes, monkeypatch, holder)

    # Reviewer hand-edits summary 0.
    client.put(f"/api/documents/{document_id}/summaries/0", json={"summaryText": "HAND EDIT"})
    before = client.get(f"/api/documents/{document_id}/summaries").get_json()
    assert before[0]["edited"] is True and before[0]["summaryText"] == "HAND EDIT"

    # Re-run only summary 0: fresh output, edit dropped.
    holder["tag"] = "NEW"
    resp = client.post(f"/api/documents/{document_id}/summaries/0/resummarize")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summaryText"] == "NEW body 1"
    assert body["edited"] is False

    after = client.get(f"/api/documents/{document_id}/summaries").get_json()
    assert after[1]["summaryText"] == "OLD body 6"  # the other summary is untouched


def test_resummarize_unknown_idx_404(client, pdf_bytes):
    document_id = _upload(client, pdf_bytes).get_json()["id"]
    assert client.post(f"/api/documents/{document_id}/summaries/999/resummarize").status_code == 404


def test_resummarize_blocked_while_job_active(app, client, pdf_bytes, monkeypatch):
    document_id = _summarized_doc(client, pdf_bytes, monkeypatch, {"tag": "OLD"})

    from mrr_ai.services import job_queue

    release = threading.Event()
    with app.app_context():
        job_queue.submit(
            document_id, "segment", lambda report: release.wait(10), model="m", prompt_version="2"
        )
    try:
        assert (
            client.post(f"/api/documents/{document_id}/summaries/0/resummarize").status_code == 409
        )
    finally:
        release.set()
