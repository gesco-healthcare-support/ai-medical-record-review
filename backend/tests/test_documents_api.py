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
from app.models import Job, ReviewRow, Summary, User
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
    assert status.json() == {
        "status": "uploaded",
        "job": None,
        "unreviewed_duplicate_groups": 0,
    }

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


async def test_export_pdf_conflict_when_no_summaries(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    resp = await client.post(
        f"/api/documents/{doc_id}/export/pdf", json={"patientName": "Synthetic Patient"}
    )
    assert resp.status_code == 409  # no summaries to export yet


async def test_export_pdf_returns_linked_pdf_with_working_links(authed):
    """The runnable link-proof: summary letter first + source appended, one GOTO link per summary
    with a non-zero hotspot targeting its source page (row_start)."""
    import pymupdf

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    with get_sessionmaker()() as session:
        job = Job(document_id=doc_id, kind="summarize", state="done", model="m", prompt_version="1")
        session.add(job)
        session.flush()
        session.add(
            Summary(
                document_id=doc_id,
                job_id=job.id,
                idx=0,
                title="Progress Report",
                text="summary body text",
                row_start=2,
                row_end=2,
                row_category=_VALID_CATEGORY,
            )
        )
        session.commit()

    resp = await client.post(
        f"/api/documents/{doc_id}/export/pdf", json={"patientName": "Synthetic Patient"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"

    doc = pymupdf.open(stream=resp.content, filetype="pdf")
    src_pages = 2
    summ = doc.page_count - src_pages
    assert summ >= 1  # a summary letter precedes the source
    gotos = [
        link
        for pno in range(doc.page_count)
        for link in doc[pno].get_links()
        if link.get("kind") == pymupdf.LINK_GOTO
    ]
    assert len(gotos) == 1
    link = gotos[0]
    assert link["page"] == summ + (2 - 1)  # source page row_start=2 -> combined index
    assert link["from"].width > 1 and link["from"].height > 1  # real, clickable hotspot
    doc.close()


def test_manualcheck_flag_is_stripped_from_both_exports():
    """The [ManualCheck] review flag never appears in the Word title or the linked-PDF link title
    (it stays an in-app flag only; a finished report/PDF cannot be edited to remove it)."""
    from app.api.documents import _export_entry, _pdf_entry

    summary = Summary(
        document_id="d",
        job_id=1,
        idx=0,
        title="[ManualCheck] MRI Report - Dr Scan (Pages 3-5)",
        text="body",
        row_start=3,
        row_end=5,
        row_category="3",
        manual_check=True,
        date="-",
    )
    word = _export_entry(summary)
    pdf = _pdf_entry(summary)
    assert "[ManualCheck]" not in word["summaryTitle"]
    assert "[ManualCheck]" not in pdf["linkTitle"]
    assert "MRI Report" in word["summaryTitle"] and "MRI Report" in pdf["linkTitle"]


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

    def fake(_pdf_path, row, _model=None, prompt=None, verify=None):
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


async def test_segment_start_enqueues_then_conflicts(authed):
    """P4b: segment/start enqueues a job; a second start while it's active returns 409."""
    from app.worker.queues import queue_for

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    queue = queue_for("segment")
    queue.empty()
    try:
        first = await client.post(f"/api/documents/{doc_id}/segment/start")
        assert first.status_code == 200 and first.json() == {"ok": True}
        assert queue.count == 1

        # The DB one-active-job index rejects a second enqueue while the first is queued.
        second = await client.post(f"/api/documents/{doc_id}/segment/start")
        assert second.status_code == 409
    finally:
        queue.empty()


async def test_summarize_start_requires_included_rows(authed):
    """P4b: with nothing marked for summarization, summarize/start is a 400 (not an empty job)."""
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    resp = await client.post(f"/api/documents/{doc_id}/summarize/start", json={})
    assert resp.status_code == 400


async def test_summarize_start_enqueues_with_rows(authed):
    """P4b: passing rows flushes them, then summarize/start enqueues on the summarize queue."""
    from app.worker.queues import queue_for

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    queue = queue_for("summarize")
    queue.empty()
    try:
        resp = await client.post(
            f"/api/documents/{doc_id}/summarize/start",
            json={"rows": [{"start": 1, "end": 1, "category": _VALID_CATEGORY}]},
        )
        assert resp.status_code == 200
        assert queue.count == 1
        assert queue.jobs[0].func_name.endswith("summarize_document")
    finally:
        queue.empty()


async def test_summarize_start_fresh_clears_existing_summaries(authed):
    """Item 7: fresh=true wipes prior summaries before enqueue so the run regenerates every row;
    without it the summaries are left for the resumable worker to reuse."""
    from app.db import get_sessionmaker
    from app.models import Job, Summary
    from app.worker.queues import queue_for

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    with get_sessionmaker()() as session:
        prior = Job(
            document_id=doc_id, kind="summarize", state="done", model="m", prompt_version="1"
        )
        session.add(prior)
        session.flush()
        session.add(
            Summary(
                document_id=doc_id,
                job_id=prior.id,
                idx=0,
                title="stale",
                date="-",
                text="old",
                row_start=1,
                row_end=1,
                row_category=_VALID_CATEGORY,
            )
        )
        session.commit()

    queue = queue_for("summarize")
    queue.empty()
    try:
        resp = await client.post(
            f"/api/documents/{doc_id}/summarize/start",
            json={"rows": [{"start": 1, "end": 1, "category": _VALID_CATEGORY}], "fresh": True},
        )
        assert resp.status_code == 200
    finally:
        queue.empty()

    with get_sessionmaker()() as session:
        remaining = session.scalars(select(Summary).where(Summary.document_id == doc_id)).all()
        assert remaining == []  # fresh cleared the stale summary


def test_merge_pdfs_computes_page_ranges():
    """P6: merge concatenates in order, tiles the page ranges, and skips unreadable files."""
    import io

    from pypdf import PdfReader

    from app.services.aggregate import merge_pdfs

    merged, records = merge_pdfs(
        [("a.pdf", _pdf_bytes(2)), ("b.pdf", _pdf_bytes(3)), ("bad.pdf", b"not a pdf")]
    )
    assert records == [
        {"filename": "a.pdf", "start": 1, "end": 2, "pages": 2},
        {"filename": "b.pdf", "start": 3, "end": 5, "pages": 3},
    ]
    assert len(PdfReader(io.BytesIO(merged)).pages) == 5


async def test_extract_header_mocked(authed, monkeypatch):
    """P6: extract-header returns the Vertex-extracted header fields (Vertex mocked)."""
    client, _ = authed
    doc_id = await _upload(client, pages=2)

    import app.api.documents as documents_module

    monkeypatch.setattr(
        documents_module,
        "extract_header",
        lambda pdf_path, pages: {
            "first_name": "Synthetic",
            "last_name": "Patient",
            "dob": "-",
            "lawfirm": "Example Law",
        },
    )
    resp = await client.post(f"/api/documents/{doc_id}/extract-header")
    assert resp.status_code == 200
    assert resp.json() == {
        "patient_first_name": "Synthetic",
        "patient_last_name": "Patient",
        "patient_dob": "-",
        "law_firm": "Example Law",
    }


async def test_extract_header_persists_on_the_document(authed, monkeypatch):
    """Detect-once: a successful extract-header saves the fields onto the document, so a later GET
    reflects them without a separate PUT /header (Vertex mocked)."""
    client, _ = authed
    doc_id = await _upload(client, pages=2)

    import app.api.documents as documents_module

    monkeypatch.setattr(
        documents_module,
        "extract_header",
        lambda pdf_path, pages: {
            "first_name": "Synthetic",
            "last_name": "Patient",
            "dob": "01/02/1990",
            "lawfirm": "Example Law",
        },
    )
    resp = await client.post(f"/api/documents/{doc_id}/extract-header")
    assert resp.status_code == 200

    got = (await client.get(f"/api/documents/{doc_id}")).json()
    assert got["patient_first_name"] == "Synthetic"
    assert got["patient_last_name"] == "Patient"
    assert got["patient_dob"] == "01/02/1990"
    assert got["law_firm"] == "Example Law"


async def test_extract_header_ocr_unavailable_does_not_persist(authed, monkeypatch):
    """A PipelineError leaves the stored header untouched (nothing half-saved)."""
    client, _ = authed
    doc_id = await _upload(client, pages=1)

    import app.api.documents as documents_module

    def boom(pdf_path, pages):
        raise OcrUnavailableError("no tesseract")

    monkeypatch.setattr(documents_module, "extract_header", boom)
    resp = await client.post(f"/api/documents/{doc_id}/extract-header")
    assert resp.status_code == 503

    got = (await client.get(f"/api/documents/{doc_id}")).json()
    assert got["patient_first_name"] == "" and got["law_firm"] == ""


async def test_extract_header_ocr_unavailable_returns_503(authed, monkeypatch):
    client, _ = authed
    doc_id = await _upload(client, pages=1)

    import app.api.documents as documents_module

    def boom(pdf_path, pages):
        raise OcrUnavailableError("no tesseract")

    monkeypatch.setattr(documents_module, "extract_header", boom)
    resp = await client.post(f"/api/documents/{doc_id}/extract-header")
    assert resp.status_code == 503


async def test_aggregate_merges_creates_rows_and_enqueues_classify(authed):
    """P6: multi-file upload merges into one Document, seeds a row per record, enqueues classify."""
    from app.worker.queues import queue_for

    client, _ = authed
    queue = queue_for("segment")  # classify routes to the segment queue
    queue.empty()
    try:
        resp = await client.post(
            "/api/documents/aggregate",
            files=[
                ("pdfs", ("a.pdf", _pdf_bytes(2), "application/pdf")),
                ("pdfs", ("b.pdf", _pdf_bytes(3), "application/pdf")),
            ],
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["page_count"] == 5 and len(body["records"]) == 2

        got = await client.get(f"/api/documents/{body['id']}")
        rows = got.json()["rows"]
        assert len(rows) == 2
        assert (rows[0]["start"], rows[0]["end"]) == (1, 2)
        assert (rows[1]["start"], rows[1]["end"]) == (3, 5)

        assert queue.count == 1
        assert queue.jobs[0].func_name.endswith("classify_document")
    finally:
        queue.empty()


def _seed_rows(doc_id, specs):
    """Seed ReviewRows for a document. `specs` = list of (start, end, dupe_group)."""
    with get_sessionmaker()() as session:
        for idx, (start, end, group) in enumerate(specs):
            session.add(
                ReviewRow(
                    document_id=doc_id,
                    idx=idx,
                    start=start,
                    end=end,
                    category=_VALID_CATEGORY,
                    title=f"Doc {idx}",
                    date=f"0{idx + 1}/01/2026",
                    injury_date="-",
                    flag="-",
                    include=True,
                    dupe_group=group,
                )
            )
        session.commit()


async def test_duplicates_list_status_and_keep_one(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1), (5, 6, None)])

    dup = await client.get(f"/api/documents/{doc_id}/duplicates")
    assert dup.status_code == 200
    clusters = dup.json()["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["group"] == 1
    assert {r["idx"] for r in clusters[0]["rows"]} == {0, 1}

    status = await client.get(f"/api/documents/{doc_id}/status")
    assert status.json()["unreviewed_duplicate_groups"] == 1  # advisory count

    resolved = await client.post(
        f"/api/documents/{doc_id}/duplicates/1/resolve",
        json={"action": "keep_one", "primary_idx": 0},
    )
    assert resolved.status_code == 200
    with get_sessionmaker()() as session:
        rows = {
            r.idx: r
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        }
    assert rows[0].dupe_primary is True and rows[0].include is True
    assert rows[1].include is False  # the other copy is excluded from summarization

    status2 = await client.get(f"/api/documents/{doc_id}/status")
    assert status2.json()["unreviewed_duplicate_groups"] == 0  # resolved -> no longer advised


async def test_duplicates_dismiss_and_error_paths(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, 2), (3, 4, 2)])

    dismissed = await client.post(
        f"/api/documents/{doc_id}/duplicates/2/resolve", json={"action": "dismiss"}
    )
    assert dismissed.status_code == 200
    with get_sessionmaker()() as session:
        rows = session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
    assert all(r.dupe_dismissed for r in rows)

    missing = await client.post(
        f"/api/documents/{doc_id}/duplicates/999/resolve", json={"action": "dismiss"}
    )
    assert missing.status_code == 404
    bad = await client.post(
        f"/api/documents/{doc_id}/duplicates/2/resolve", json={"action": "nope"}
    )
    assert bad.status_code == 400

    started = await client.post(f"/api/documents/{doc_id}/dedup/start")
    assert started.status_code == 200 and started.json()["ok"] is True


async def test_duplicates_keep_one_bad_primary_is_400(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 1, 6), (2, 2, 6)])
    bad = await client.post(
        f"/api/documents/{doc_id}/duplicates/6/resolve",
        json={"action": "keep_one", "primary_idx": 999},  # not a member of the cluster
    )
    assert bad.status_code == 400


async def test_duplicates_paths_while_a_job_is_active(authed):
    """A queued job makes the document active: GET /duplicates still surfaces the dedup job's
    progress, but resolve and dedup/start are blocked (409)."""
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 1, 5), (2, 2, 5)])
    with get_sessionmaker()() as session:
        session.add(
            Job(document_id=doc_id, kind="dedup", state="queued", model="m", prompt_version="1")
        )
        session.commit()

    dup = await client.get(f"/api/documents/{doc_id}/duplicates")
    assert dup.status_code == 200 and dup.json()["job"] is not None  # progress surfaced

    resolved = await client.post(
        f"/api/documents/{doc_id}/duplicates/5/resolve", json={"action": "dismiss"}
    )
    assert resolved.status_code == 409  # blocked while a job runs

    started = await client.post(f"/api/documents/{doc_id}/dedup/start")
    assert started.status_code == 409  # one-active-job conflict


def test_dupe_date_key_parses_dates_and_defaults_unknown():
    from app.api.documents import _dupe_date_key

    assert _dupe_date_key("03/10/2026") == (2026, 3, 10)
    assert _dupe_date_key("1/2/26") == (2026, 1, 2)  # 2-digit year -> 2000s
    assert _dupe_date_key("-") == (9999, 12, 31)  # unknown sorts last
    assert _dupe_date_key("") == (9999, 12, 31)
