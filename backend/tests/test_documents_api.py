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


def _set_dedup_fields(doc_id, ranges, group=1):
    """Mark the given (start, end) rows as a confirmed duplicate cluster with stored OCR text."""
    with get_sessionmaker()() as session:
        for start, end in ranges:
            row = session.scalar(
                select(ReviewRow).where(
                    ReviewRow.document_id == doc_id,
                    ReviewRow.start == start,
                    ReviewRow.end == end,
                )
            )
            row.source_text = f"ocr text {start}-{end}"
            row.dupe_group = group
            row.dupe_primary = start == ranges[0][0]
            row.dupe_dismissed = False
        session.commit()


def _dedup_state(doc_id):
    """[(start, end, source_text is not None, dupe_group, dupe_primary, dupe_dismissed)] by idx."""
    with get_sessionmaker()() as session:
        rows = session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
        ).all()
        return [
            (
                r.start,
                r.end,
                r.source_text is not None,
                r.dupe_group,
                r.dupe_primary,
                r.dupe_dismissed,
            )
            for r in rows
        ]


async def test_row_save_preserves_dedup_fields_on_unchanged_ranges(authed):
    # WHEN only category/include/title change, THE SYSTEM SHALL retain the dedup annotations.
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_dedup_fields(doc_id, [(1, 2), (3, 4)])

    edited = [
        {"start": 1, "end": 2, "category": "3", "include": False, "title": "Renamed"},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY, "include": True},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": edited})
    ).status_code == 200

    state = _dedup_state(doc_id)
    assert state[0] == (1, 2, True, 1, True, False)
    assert state[1] == (3, 4, True, 1, False, False)


async def test_summarize_start_sees_rows_written_in_the_same_request(authed):
    """Regression: _store_rows loads document.review_rows to snapshot the dedup fields, so the
    collection must be expired after the rewrite - otherwise summarize_start's "at least one row is
    included" check reads the stale (pre-delete) collection and wrongly 400s."""
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    resp = await client.post(
        f"/api/documents/{doc_id}/summarize/start",
        json={"rows": [{"start": 1, "end": 2, "category": _VALID_CATEGORY, "include": True}]},
    )
    assert resp.status_code != 400, resp.text  # must not be "no rows are marked for summarization"


async def test_row_save_drops_dedup_fields_when_boundary_changes(authed):
    # WHEN a row's (start,end) no longer matches a pre-save row, its dedup fields reset; untouched
    # rows keep theirs (the merge case: three copies -> one merged row leaves a 2-copy cluster).
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY},
        {"start": 5, "end": 5, "category": _VALID_CATEGORY},
        {"start": 6, "end": 6, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_dedup_fields(doc_id, [(1, 2), (3, 4), (5, 5)])

    # Merge pages 5-6 into one row: that row is new, so it loses the grouping.
    merged = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY},
        {"start": 5, "end": 6, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": merged})
    ).status_code == 200

    state = _dedup_state(doc_id)
    assert state[0] == (1, 2, True, 1, True, False)  # untouched copy keeps its group
    assert state[1] == (3, 4, True, 1, False, False)  # untouched copy keeps its group
    assert state[2] == (5, 6, False, None, False, False)  # merged row: defaults


async def test_duplicates_hides_single_member_groups(authed):
    # WHEN a group has one member it is not a cluster; >=2 members are returned.
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 3, "category": _VALID_CATEGORY},
        {"start": 4, "end": 4, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_dedup_fields(doc_id, [(1, 2)], group=7)  # a lone member -> must not surface
    body = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert body["clusters"] == []

    _set_dedup_fields(doc_id, [(3, 3), (4, 4)], group=8)  # a real pair -> surfaces
    body = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert [c["group"] for c in body["clusters"]] == [8]
    assert len(body["clusters"][0]["rows"]) == 2


async def test_unreviewed_count_follows_inclusion_not_the_primary_mark(authed):
    """A cluster needs the reviewer only while 2+ of its copies would still be summarized, so a
    keep-one stays resolved even after a re-check clears the group and re-derives it."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_dedup_fields(doc_id, [(1, 2), (3, 4)])

    async def count():
        return (await client.get(f"/api/documents/{doc_id}/status")).json()[
            "unreviewed_duplicate_groups"
        ]

    def patch(**values):
        with get_sessionmaker()() as session:
            for row in session.scalars(
                select(ReviewRow).where(ReviewRow.document_id == doc_id, ReviewRow.start == 3)
            ).all():
                for key, value in values.items():
                    setattr(row, key, value)
            session.commit()

    patch(dupe_primary=False)  # two included copies, no primary marked -> needs review
    assert await count() == 1

    patch(include=False)  # only one copy would be summarized -> resolved
    assert await count() == 0

    patch(include=True, dupe_dismissed=True)  # "not duplicates" -> never advised again
    assert await count() == 0


async def test_duplicates_stale_flag_tracks_unchecked_included_rows(authed):
    # stale = a dedup finished AND an included row has no stored OCR text (boundary change).
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_dedup_fields(doc_id, [(1, 2), (3, 4)])

    # No dedup job yet -> nothing to be stale against.
    assert (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["stale"] is False

    with get_sessionmaker()() as session:
        session.add(
            Job(
                document_id=doc_id,
                kind="dedup",
                state="done",
                stage="deduping",
                model="m",
                prompt_version="1",
            )
        )
        session.commit()

    # Every included row still has stored text -> not stale.
    assert (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["stale"] is False

    # A boundary change leaves an included row with no stored text -> stale.
    changed = [
        {"start": 1, "end": 3, "category": _VALID_CATEGORY},
        {"start": 4, "end": 4, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": changed})
    ).status_code == 200
    assert (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["stale"] is True


async def test_duplicates_stale_flag_covers_every_row_including_dismissed(authed):
    """Staleness follows the dedup SCOPE, which is now EVERY row: a row dedup never saw - excluded or
    dismissed - means the list may be incomplete and a manual re-check is worthwhile."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY, "include": False},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_dedup_fields(doc_id, [(1, 2)])  # only the included row was checked
    with get_sessionmaker()() as session:
        session.add(
            Job(
                document_id=doc_id,
                kind="dedup",
                state="done",
                stage="deduping",
                model="m",
                prompt_version="1",
            )
        )
        session.commit()

    async def stale():
        return (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["stale"]

    assert await stale() is True  # the excluded row has no stored text -> re-check is worthwhile

    with get_sessionmaker()() as session:
        row = session.scalar(
            select(ReviewRow).where(ReviewRow.document_id == doc_id, ReviewRow.start == 3)
        )
        row.dupe_dismissed = True
        session.commit()
    # Dismissing does NOT settle staleness any more: that row is back in dedup's scope and still has
    # no stored text, so the re-check offer stands.
    assert await stale() is True

    with get_sessionmaker()() as session:
        row = session.scalar(
            select(ReviewRow).where(ReviewRow.document_id == doc_id, ReviewRow.start == 3)
        )
        row.source_text = "ocr text 3-4"
        session.commit()
    assert await stale() is False  # every row has been looked at -> nothing missing


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


def _diagnostic_summary(**over):
    """A stored diagnostic summary whose title already carries an engine page suffix."""
    fields = dict(
        document_id="d",
        job_id=1,
        idx=0,
        title="MRI Report - Dr Scan (Pages 3-5)",
        text="body",
        row_start=3,
        row_end=5,
        row_category="3",
        date="-",
    )
    fields.update(over)
    return Summary(**fields)


def test_page_range_is_omitted_by_default_and_included_on_request():
    """A presentable report carries no per-record page ranges; the reviewer opts in per export."""
    from app.api.documents import _export_entry, _export_title_and_text, _pdf_entry

    summary = _diagnostic_summary()
    plain, _ = _export_title_and_text(summary)
    with_pages, _ = _export_title_and_text(summary, with_pages=True)

    assert "(Pages" not in plain
    # The internal [Diagnostic Study] tag no longer survives either: like [ManualCheck] it is a
    # review marker, and the human-written deliverables this output is measured against carry none.
    assert "[Diagnostic Study]" not in plain
    assert with_pages == "MRI Report - Dr Scan (Pages 3-5)"

    assert _export_entry(summary)["summaryTitle"] == plain
    assert _export_entry(summary, with_pages=True)["summaryTitle"] == with_pages
    assert _pdf_entry(summary)["linkTitle"] == plain
    assert _pdf_entry(summary, with_pages=True)["linkTitle"] == with_pages
    # Dropping the text must not disturb the hyperlink target.
    assert _pdf_entry(summary)["startPage"] == 3


def test_stale_page_suffix_is_stripped_either_way():
    """A stored suffix can be stale (wrong range after a row edit, or an en-dash range from the web
    view), so the export rebuilds it from row_start/row_end instead of trusting the title."""
    from app.api.documents import _export_title_and_text

    stale = _diagnostic_summary(edited_title="MRI Report (Pages 1-9)")
    assert "(Pages" not in _export_title_and_text(stale)[0]
    assert _export_title_and_text(stale, with_pages=True)[0].endswith("(Pages 3-5)")
    en_dash = _diagnostic_summary(edited_title="MRI Report (Pages 1\u20139)")
    assert "(Pages" not in _export_title_and_text(en_dash)[0]


def _exported(**over):
    """One Summary through the shared export path -> its exported body."""
    from app.api.documents import _export_title_and_text

    fields = dict(
        document_id="d",
        job_id=1,
        idx=0,
        title="Work Status Report (Pages 1-2)",
        text="**DOI**:05/08/2022, Body.",
        row_start=1,
        row_end=2,
        row_category="1",
        date="-",
    )
    fields.update(over)
    return _export_title_and_text(Summary(**fields))[1]


def test_export_restores_every_stated_injury_date():
    """The Summaries UI strips the DOI prefix into its edit box, so an edited body has none and the
    export re-applies it from the raw text - it must carry EVERY date the document stated, not just
    the first."""
    body = _exported(
        text="**DOI**:05/08/2022, 06/01/2023, Body.", edited_text="Reviewer's rewrite."
    )
    assert body == "**DOI**:05/08/2022, 06/01/2023, Reviewer's rewrite."


def test_export_does_not_double_up_or_invent_a_doi():
    # The effective text already carries the prefix -> no second one.
    assert _exported() == "**DOI**:05/08/2022, Body."
    # Nothing stated -> nothing added.
    assert _exported(text="Body with no injury date.") == "Body with no injury date."


def test_export_restores_a_house_grammar_prefix():
    # WHEN a summary stored in the house grammar has its prefix stripped by a reviewer edit,
    # THE SYSTEM SHALL restore it in the same grammar, including a cumulative-trauma period.
    assert (
        _exported(text="**DOI**: 05/08/22. Body.", edited_text="Rewrite.")
        == "**DOI**: 05/08/22. Rewrite."
    )
    assert (
        _exported(text="**DOI**: CT 01/02/20-03/04/21. Body.", edited_text="Rewrite.")
        == "**DOI**: CT 01/02/20-03/04/21. Rewrite."
    )


def test_export_title_carries_no_internal_tags():
    # WHEN a category-3 summary is exported, THE SYSTEM SHALL emit a title with neither internal
    # tag: [Diagnostic Study] used to be re-applied here, and the human-written deliverables this
    # output is measured against carry neither marker.
    from app.api.documents import _export_title_and_text

    tagged = _diagnostic_summary(title="[ManualCheck] MRI OF THE LUMBAR SPINE [Diagnostic Study]")
    title = _export_title_and_text(tagged)[0]
    assert "[Diagnostic Study]" not in title
    assert "[ManualCheck]" not in title
    assert title == "MRI OF THE LUMBAR SPINE"


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

    def fake(_pdf_path, row, _model=None, prompt=None, verify=None, standalone_studies=None):
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

    def fake(_pdf_path, _row, _model=None, prompt=None, standalone_studies=None):
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


def _seed_summary(doc_id, *, verified_title=None, row_category=None, idx=0, pages=(1, 1)):
    """Seed one Summary (and its owning Job) as a resummarize / category-change target."""
    with get_sessionmaker()() as session:
        job = Job(document_id=doc_id, kind="summarize", state="done", model="m", prompt_version="1")
        session.add(job)
        session.flush()
        session.add(
            Summary(
                document_id=doc_id,
                job_id=job.id,
                idx=idx,
                title="old title",
                text="old text",
                verified=verified_title is not None,
                verified_title=verified_title,
                row_start=pages[0],
                row_end=pages[1],
                row_category=row_category or _VALID_CATEGORY,
            )
        )
        session.commit()


def _fake_summarize(**over):
    """A summarize_row stand-in returning a fresh draft; `over` adds or replaces output keys."""

    def fake(_pdf_path, _row, _model=None, prompt=None, standalone_studies=None):
        return {
            "summaryTitle": "New Title (Pages 1-1)",
            "summaryDate": "-",
            "summaryText": "new body",
            "manualCheck": "",
            "sourceText": "x",
            **over,
        }

    return fake


async def test_resummarize_drops_a_stale_verified_title(authed, monkeypatch):
    """WHEN a summary carrying a verified_title is re-drafted and the new output has none, THE SYSTEM
    SHALL return the new raw title.

    effective_title() prefers verified_title over title, and resummarize reset verified/verified_text/
    verify_issues but not verified_title - so a re-draft showed the NEW body under the OLD verified
    header. It is the only one of the fifteen fields _build_summary sets that resummarize missed.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    _seed_summary(doc_id, verified_title="STALE verified title")

    import app.api.documents as documents_module

    monkeypatch.setattr(documents_module, "summarize_row", _fake_summarize())
    resp = await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")

    assert resp.status_code == 200
    assert resp.json()["summaryTitle"].startswith("New Title")
    with get_sessionmaker()() as session:
        assert session.scalar(select(Summary.verified_title)) is None


async def test_resummarize_keeps_a_fresh_verified_title(authed, monkeypatch):
    """WHEN a re-draft's output carries a verifiedTitle, THE SYSTEM SHALL store and return it - the
    same assignment must not throw the corrected header away."""
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    _seed_summary(doc_id, verified_title="STALE verified title")

    import app.api.documents as documents_module

    monkeypatch.setattr(
        documents_module, "summarize_row", _fake_summarize(verifiedTitle="Corrected Title")
    )
    resp = await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")

    assert resp.status_code == 200
    assert resp.json()["summaryTitle"] == "Corrected Title"


async def test_put_summary_category_writes_through_to_the_row(authed):
    """WHEN a valid category is PUT, THE SYSTEM SHALL set ReviewRow.category, leave the summary's
    generating snapshot alone, and audit the change with both ids.

    The snapshot must NOT move: the gap between it and the row IS the staleness signal the badge
    reads, so updating both would erase the very thing that says "re-draft me".
    """
    from app.models import AuditLog

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))

    resp = await client.put(
        f"/api/documents/{doc_id}/summaries/0", json={"category": _OTHER_CATEGORY}
    )

    assert resp.status_code == 200, resp.text
    with get_sessionmaker()() as session:
        assert session.scalar(select(ReviewRow.category)) == _OTHER_CATEGORY
        assert session.scalar(select(Summary.row_category)) == _VALID_CATEGORY  # snapshot untouched
        entry = session.scalar(select(AuditLog).where(AuditLog.action == "summary.category"))
        assert entry is not None
        assert _VALID_CATEGORY in entry.detail and _OTHER_CATEGORY in entry.detail


async def test_audit_detail_defaults_to_null_for_existing_callers(authed):
    """WHEN audit() is called without detail, THE SYSTEM SHALL write NULL there.

    The parameter is keyword-only precisely so the fourteen positional callers cannot be broken by
    adding it; upload is one of them.
    """
    from app.models import AuditLog

    client, _ = authed
    await _upload(client, pages=1)

    with get_sessionmaker()() as session:
        entry = session.scalar(select(AuditLog).where(AuditLog.action == "upload"))
        assert entry is not None
        assert entry.detail is None


async def test_put_summary_rejects_an_unknown_category(authed):
    """IF the category is not an active catalog id, THEN THE SYSTEM SHALL return 400 and change
    nothing - an unknown id would resolve to no prompt and the next re-draft would fail opaquely."""
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))

    resp = await client.put(
        f"/api/documents/{doc_id}/summaries/0", json={"category": "not-a-category"}
    )

    assert resp.status_code == 400
    with get_sessionmaker()() as session:
        assert session.scalar(select(ReviewRow.category)) == _VALID_CATEGORY


async def test_put_summary_category_refuses_while_a_job_runs(authed):
    """IF any job is active, THEN THE SYSTEM SHALL return 409 for a category write.

    Stricter than this endpoint's existing summarize-only guard, and deliberately so: a category write
    lands on a ReviewRow, and a finishing SEGMENT job replaces the whole row set via _store_rows, so
    the edit would be silently overwritten.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))
    with get_sessionmaker()() as session:
        session.add(
            Job(
                document_id=doc_id,
                kind="segment",
                state="running",
                model="m",
                prompt_version="1",
            )
        )
        session.commit()

    resp = await client.put(
        f"/api/documents/{doc_id}/summaries/0", json={"category": _OTHER_CATEGORY}
    )

    assert resp.status_code == 409
    with get_sessionmaker()() as session:
        assert session.scalar(select(ReviewRow.category)) == _VALID_CATEGORY


async def test_put_summary_category_refuses_when_no_row_matches(authed):
    """IF no ReviewRow matches the summary's stored page range, THEN THE SYSTEM SHALL return 409.

    Writing only the snapshot would leave Review & correct and Summaries permanently disagreeing,
    which is worse than an error the reviewer can act on by re-segmenting.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=3)
    _seed_rows(doc_id, [(1, 1, None)])  # the summary below covers 2-3, so nothing matches
    _seed_summary(doc_id, pages=(2, 3))

    resp = await client.put(
        f"/api/documents/{doc_id}/summaries/0", json={"category": _OTHER_CATEGORY}
    )

    assert resp.status_code == 409
    assert "boundar" in resp.json()["detail"].lower()


async def test_get_summaries_reports_the_rows_live_category(authed):
    """WHEN a row's category changed after generation, THE SYSTEM SHALL return the CURRENT value as
    rowCategoryLive while row.category still holds the generating snapshot."""
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))

    before = (await client.get(f"/api/documents/{doc_id}/summaries")).json()
    assert before[0]["rowCategoryLive"] == _VALID_CATEGORY  # in step -> no badge
    assert before[0]["row"]["category"] == _VALID_CATEGORY

    await client.put(f"/api/documents/{doc_id}/summaries/0", json={"category": _OTHER_CATEGORY})
    after = (await client.get(f"/api/documents/{doc_id}/summaries")).json()

    assert after[0]["rowCategoryLive"] == _OTHER_CATEGORY  # diverged -> badge
    assert after[0]["row"]["category"] == _VALID_CATEGORY


async def test_every_summary_response_carries_the_live_category(authed, monkeypatch):
    """WHEN any route returns a summary, THE SYSTEM SHALL include rowCategoryLive.

    The client patches its cache with whatever a mutation returns, replacing the item wholesale. A
    single route answering with the bare listing() therefore DELETES this field from the cache - which
    is how the badge failed to appear after a successful category save, with the write itself correct
    in the database. Three routes return a summary; all three must agree on the shape.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))

    import app.api.documents as documents_module

    monkeypatch.setattr(documents_module, "summarize_row", _fake_summarize())

    listing = (await client.get(f"/api/documents/{doc_id}/summaries")).json()[0]
    put = (
        await client.put(f"/api/documents/{doc_id}/summaries/0", json={"category": _OTHER_CATEGORY})
    ).json()
    redraft = (await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")).json()

    for name, body in (("get", listing), ("put", put), ("resummarize", redraft)):
        assert "rowCategoryLive" in body, f"{name} dropped rowCategoryLive"
    # The PUT must report the category it just wrote, not the pre-change value it was holding.
    assert put["rowCategoryLive"] == _OTHER_CATEGORY
    # A re-draft re-snapshots row_category from the row, so the two agree again -> badge clears.
    assert redraft["rowCategoryLive"] == redraft["row"]["category"] == _OTHER_CATEGORY


async def test_redraft_after_a_category_change_uses_the_new_categorys_prompt(authed, monkeypatch):
    """WHEN a summary is re-drafted after its category changed, THE SYSTEM SHALL resolve the prompt for
    the NEW category and re-snapshot row_category, so the badge clears.

    This is the whole feature, and the other tests do not cover it: row_category could be updated
    correctly while the prompt lookup still used the old id, and every assertion would still pass. So
    capture the prompt actually handed to summarize_row and prove it is the new category's.
    """
    from app.services import catalog
    from app.db import get_sessionmaker as _sm

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))

    with _sm()() as session:
        old_prompt = catalog.get_prompt(session, "summary", _VALID_CATEGORY)
        new_prompt = catalog.get_prompt(session, "summary", _OTHER_CATEGORY)
    assert old_prompt != new_prompt, (
        "fixture categories must have distinct prompts for this to prove anything"
    )

    seen = {}

    def fake(_pdf_path, row, _model=None, prompt=None, standalone_studies=None):
        seen["prompt"] = prompt
        seen["row_category"] = row["category"]
        return _fake_summarize()(_pdf_path, row, _model, prompt, standalone_studies)

    import app.api.documents as documents_module

    monkeypatch.setattr(documents_module, "summarize_row", fake)

    await client.put(f"/api/documents/{doc_id}/summaries/0", json={"category": _OTHER_CATEGORY})
    redraft = (await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")).json()

    assert seen["row_category"] == _OTHER_CATEGORY  # the engine was handed the NEW category
    assert seen["prompt"] == new_prompt  # ...and the NEW category's prompt, not the old one
    # Snapshot caught up with the row -> the two agree -> the frontend badge condition goes false.
    assert redraft["row"]["category"] == redraft["rowCategoryLive"] == _OTHER_CATEGORY


async def test_get_summaries_reports_null_when_no_row_matches(authed):
    """WHEN no ReviewRow matches a summary's page range, THE SYSTEM SHALL return rowCategoryLive as
    null - there is no live category to compare against, so the UI must not claim a mismatch."""
    client, _ = authed
    doc_id = await _upload(client, pages=3)
    _seed_rows(doc_id, [(1, 1, None)])
    _seed_summary(doc_id, pages=(2, 3))

    body = (await client.get(f"/api/documents/{doc_id}/summaries")).json()

    assert body[0]["rowCategoryLive"] is None


async def test_cancel_flags_a_running_job(authed):
    """WHEN cancel is posted for a running job, THE SYSTEM SHALL set cancel_requested, publish the
    Redis signal the retry backoff reads, and return the job."""
    from app.models import AuditLog
    from app.worker import cancel as cancel_mod

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    with get_sessionmaker()() as session:
        job = Job(
            document_id=doc_id, kind="segment", state="running", model="m", prompt_version="1"
        )
        session.add(job)
        session.commit()
        job_id = job.id

    try:
        resp = await client.post(f"/api/documents/{doc_id}/jobs/{job_id}/cancel")
        assert resp.status_code == 200, resp.text
        assert resp.json()["kind"] == "segment"
        assert cancel_mod.is_cancel_requested(job_id) is True
        with get_sessionmaker()() as session:
            assert session.get(Job, job_id).cancel_requested is True
            entry = session.scalar(select(AuditLog).where(AuditLog.action == "job.cancel"))
            assert entry is not None and "force False" in entry.detail
    finally:
        cancel_mod.clear_cancel(job_id)


async def test_cancel_of_another_documents_job_is_404(authed):
    """IF the job belongs to a different document, THEN THE SYSTEM SHALL return 404 - the job id is
    guessable, so it must be scoped to the document the ownership guard already checked."""
    client, _ = authed
    mine = await _upload(client, pages=1)
    other = await _upload(client, pages=1)
    with get_sessionmaker()() as session:
        job = Job(document_id=other, kind="segment", state="running", model="m", prompt_version="1")
        session.add(job)
        session.commit()
        job_id = job.id

    assert (await client.post(f"/api/documents/{mine}/jobs/{job_id}/cancel")).status_code == 404


async def test_cancel_of_a_finished_job_is_a_no_op_not_an_error(authed):
    """IF the job is already terminal, THEN THE SYSTEM SHALL return 200 and change nothing.

    A job can finish between the reviewer's click and the request landing. That race is normal, and
    answering it with a 409 would show an alarming message for a stop that merely arrived late.
    """
    from app.worker import cancel as cancel_mod

    client, _ = authed
    doc_id = await _upload(client, pages=1)
    with get_sessionmaker()() as session:
        job = Job(document_id=doc_id, kind="segment", state="done", model="m", prompt_version="1")
        session.add(job)
        session.commit()
        job_id = job.id

    resp = await client.post(f"/api/documents/{doc_id}/jobs/{job_id}/cancel")

    assert resp.status_code == 200
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).cancel_requested is False  # untouched
    assert cancel_mod.is_cancel_requested(job_id) is False  # no signal published either


async def test_force_cancel_still_succeeds_when_the_work_horse_is_gone(authed, monkeypatch):
    """WHEN force is posted and the stop command cannot be delivered, THE SYSTEM SHALL still return
    200 - the DB row and the cooperative flag are already set, and orphan recovery reaps the rest."""
    import app.api.documents as documents_module
    from app.models import AuditLog
    from app.worker import cancel as cancel_mod

    client, _ = authed
    doc_id = await _upload(client, pages=1)
    with get_sessionmaker()() as session:
        job = Job(
            document_id=doc_id, kind="summarize", state="running", model="m", prompt_version="1"
        )
        session.add(job)
        session.commit()
        job_id = job.id

    def boom(*_a, **_k):
        raise RuntimeError("no such work-horse")

    monkeypatch.setattr(documents_module, "send_stop_job_command", boom)
    try:
        resp = await client.post(
            f"/api/documents/{doc_id}/jobs/{job_id}/cancel", json={"force": True}
        )
        assert resp.status_code == 200
        with get_sessionmaker()() as session:
            entry = session.scalar(select(AuditLog).where(AuditLog.action == "job.cancel"))
            assert "force True" in entry.detail
    finally:
        cancel_mod.clear_cancel(job_id)


async def test_dedup_start_fresh_clears_the_stored_ocr(authed):
    """WHEN dedup is started with fresh, THE SYSTEM SHALL clear each row's source_text so the run
    re-OCRs. Reusing that text is exactly what makes a Continue nearly free, so Start over has to
    discard it or the two buttons would do the same thing."""
    from tests.conftest import lanes

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 1, None), (2, 2, None)])
    with get_sessionmaker()() as session:
        for row in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all():
            row.source_text = "previous extraction"
        session.commit()
    lanes("segment")  # the dedup enqueue lands on the segment lane; keep the fixture consistent

    resp = await client.post(f"/api/documents/{doc_id}/dedup/start", json={"fresh": True})

    assert resp.status_code == 200
    with get_sessionmaker()() as session:
        texts = [
            r.source_text
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        ]
    assert texts == [None, None]


async def test_dedup_start_without_fresh_keeps_the_stored_ocr(authed):
    """WHEN dedup is started without fresh, THE SYSTEM SHALL keep source_text - the default is a
    continue."""
    from tests.conftest import lanes

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 1, None)])
    with get_sessionmaker()() as session:
        row = session.scalar(select(ReviewRow).where(ReviewRow.document_id == doc_id))
        row.source_text = "previous extraction"
        session.commit()
    lanes("segment")

    resp = await client.post(f"/api/documents/{doc_id}/dedup/start")

    assert resp.status_code == 200
    with get_sessionmaker()() as session:
        assert session.scalar(select(ReviewRow.source_text)) == "previous extraction"


async def test_segment_start_enqueues_then_conflicts(authed):
    """P4b: segment/start enqueues a job; a second start while it's active returns 409."""
    from tests.conftest import lanes

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    queue = lanes("segment")
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
    from tests.conftest import lanes

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    queue = lanes("summarize")
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
    from tests.conftest import lanes

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

    queue = lanes("summarize")
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
    from tests.conftest import lanes

    client, _ = authed
    queue = lanes("segment")  # classify routes to the segment queue
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
        # General (100) seeds off-by-default (classify re-derives per row afterwards).
        assert rows[0]["include"] is False and rows[1]["include"] is False

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


def _set_include(doc_id, include):
    with get_sessionmaker()() as session:
        for row in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all():
            row.include = include
        session.commit()


async def test_editing_one_copys_pages_reopens_the_whole_dismissed_cluster(authed):
    """WHEN a dismissed cluster loses a copy to a boundary edit, the copies that remain SHALL drop the
    dismissal - otherwise the eroded cluster still looks intact and the next re-check silently
    re-dismisses a set the reviewer never judged (found live, 2026-07-28)."""
    client, _ = authed
    doc_id = await _upload(client, pages=9)
    rows = [
        {"start": 1, "end": 3, "category": _VALID_CATEGORY},
        {"start": 4, "end": 6, "category": _VALID_CATEGORY},
        {"start": 7, "end": 9, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    with get_sessionmaker()() as session:
        for row in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all():
            row.source_text = "same scanned text"
            row.dupe_group = 1
            row.dupe_dismissed = True
            row.dupe_similarity = 1.0
        session.commit()

    # Shrink the third copy: same cluster, different membership.
    shrunk = [*rows[:2], {"start": 7, "end": 8, "category": _VALID_CATEGORY}]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": shrunk})
    ).status_code == 200

    with get_sessionmaker()() as session:
        by_range = {
            (r.start, r.end): r
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        }
    assert by_range[(1, 3)].dupe_dismissed is False  # surviving copies reopened
    assert by_range[(4, 6)].dupe_dismissed is False
    assert by_range[(7, 8)].dupe_group is None  # the edited row starts fresh
    # The grouping itself is kept so the tab still shows the cluster until the re-check runs.
    assert by_range[(1, 3)].dupe_group == 1
    assert by_range[(1, 3)].dupe_similarity == 1.0  # the score survives an unrelated metadata edit


async def test_keep_one_does_not_opt_an_excluded_cluster_into_the_report(authed):
    """WHEN every copy in a cluster is excluded and the reviewer keeps one, all copies stay excluded.
    Three copies of a routing slip are category 100 (unchecked by default); turning the kept one on
    would summarize paperwork nobody asked for."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1), (5, 6, None)])
    _set_include(doc_id, False)

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
    assert rows[0].dupe_primary is True  # the kept copy is still recorded
    assert rows[0].include is False and rows[1].include is False  # but nothing was opted in
    # Resolving still clears the cluster from the advisory count.
    status = await client.get(f"/api/documents/{doc_id}/status")
    assert status.json()["unreviewed_duplicate_groups"] == 0


async def test_duplicates_report_the_cluster_similarity(authed):
    """The API returns the stored score per cluster (null before a re-run has computed one)."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1), (5, 6, None)])

    before = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["clusters"]
    assert before[0]["similarity"] is None

    with get_sessionmaker()() as session:
        for row in session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == doc_id, ReviewRow.dupe_group == 1)
        ).all():
            row.dupe_similarity = 0.97
        session.commit()

    after = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["clusters"]
    assert after[0]["similarity"] == 0.97


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


def _finish_dedup(doc_id):
    """A completed dedup job, so the derived `unreadable` count is reported."""
    with get_sessionmaker()() as session:
        session.add(
            Job(
                document_id=doc_id,
                kind="dedup",
                state="done",
                stage="deduping",
                model="m",
                prompt_version="1",
            )
        )
        session.commit()


async def test_remove_member_drops_one_copy_and_keeps_the_rest_clustered(authed):
    """WHEN a reviewer removes one copy from a 3-member cluster, THE SYSTEM SHALL clear only that
    row's group and leave the other two clustered - the mixed-cluster case a whole-group dismiss
    cannot express."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1), (5, 6, 1)])

    removed = await client.post(
        f"/api/documents/{doc_id}/duplicates/1/resolve",
        json={"action": "remove_member", "idx": 2},
    )
    assert removed.status_code == 200

    with get_sessionmaker()() as session:
        rows = {
            r.idx: r
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        }
    assert rows[2].dupe_group is None
    assert rows[0].dupe_group == 1 and rows[1].dupe_group == 1
    # The removed copy stays in the report: it is a distinct document, not an excluded duplicate.
    assert rows[2].include is True

    clusters = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["clusters"]
    assert {r["idx"] for r in clusters[0]["rows"]} == {0, 1}


async def test_remove_member_clears_the_group_when_one_copy_would_remain(authed):
    """A cluster of one is not a duplicate set, so removing the second-to-last member SHALL clear the
    group outright rather than leave a row pointing at a group no surface will render."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1)])

    assert (
        await client.post(
            f"/api/documents/{doc_id}/duplicates/1/resolve",
            json={"action": "remove_member", "idx": 1},
        )
    ).status_code == 200

    with get_sessionmaker()() as session:
        groups = [
            r.dupe_group
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        ]
    assert groups == [None, None]
    assert (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["clusters"] == []


async def test_remove_member_rejects_an_idx_outside_the_cluster(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1), (5, 6, None)])

    bad = await client.post(
        f"/api/documents/{doc_id}/duplicates/1/resolve",
        json={"action": "remove_member", "idx": 2},  # idx 2 is not in group 1
    )
    assert bad.status_code == 400
    missing = await client.post(
        f"/api/documents/{doc_id}/duplicates/1/resolve", json={"action": "remove_member"}
    )
    assert missing.status_code == 400


async def test_unknown_resolve_action_names_all_three(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1)])

    bad = await client.post(
        f"/api/documents/{doc_id}/duplicates/1/resolve", json={"action": "explode"}
    )
    assert bad.status_code == 400
    assert "remove_member" in bad.json()["detail"]


async def test_duplicates_report_sub_documents_that_could_not_be_read(authed):
    """WHEN a completed check left a row with no OCR text, THE SYSTEM SHALL report it as unreadable.

    Empty text matches nothing (the word-set signature is a null set), so such a row was never
    compared against anything - measured live at 18 of 91 rows on one record, where the run still
    presented as clean.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, None), (3, 4, None), (5, 6, None)])

    # No completed dedup run yet -> nothing to report against.
    assert (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["unreadable"] == 0
    _finish_dedup(doc_id)

    with get_sessionmaker()() as session:
        rows = session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
        ).all()
        rows[0].source_text = "readable scanned text"
        rows[1].source_text = "   \n "  # read cleanly, carried no words
        rows[2].source_text = None  # never attempted -> `stale`'s business, not this count
        session.commit()

    body = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert body["unreadable"] == 1
    assert body["stale"] is True  # the untouched row is what staleness is for


def test_dupe_date_key_parses_dates_and_defaults_unknown():
    from app.api.documents import _dupe_date_key

    assert _dupe_date_key("03/10/2026") == (2026, 3, 10)
    assert _dupe_date_key("1/2/26") == (2026, 1, 2)  # 2-digit year -> 2000s
    assert _dupe_date_key("-") == (9999, 12, 31)  # unknown sorts last
    assert _dupe_date_key("") == (9999, 12, 31)
