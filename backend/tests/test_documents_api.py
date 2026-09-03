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
from app.models import AuditLog, Document, Job, ReviewRow, Summary, User
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
    assert body["id"] == doc_id
    assert body["rows"] == []
    assert "categories" in body

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
    assert ok.status_code == 200
    assert ok.json()["count"] == 1

    bad_range = await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 5, "end": 2, "category": _VALID_CATEGORY}]},
    )
    assert bad_range.status_code == 400

    got = await client.get(f"/api/documents/{doc_id}")
    assert len(got.json()["rows"]) == 1  # the valid PUT persisted; the bad one did not replace it


async def test_rows_carry_the_rule_verdict_on_the_title(authed):
    """Each row reports whether a high-precision rule named its TITLE as administrative paperwork.

    This is what lets the review editor separate "a rule said this is paperwork" from "nothing
    identified this document" inside category 100 - the same value on screen today (issue #144).
    Title-derived only; the editor combines it with the live `category`.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=3)
    saved = await client.put(
        f"/api/documents/{doc_id}/rows",
        json={
            "rows": [
                # A rule answers 100: genuinely administrative, not part of the set to check by hand.
                {"start": 1, "end": 1, "category": "100", "title": "Proof of Service"},
                # No rule answers at all: the cascade guessed, which is the set #144 is about.
                {"start": 2, "end": 2, "category": "100", "title": "Placeholder Report"},
                # A rule answers something OTHER than 100. The row sits at 100 only because it was
                # segmented before that rule shipped (#161 sent History & Physical to 1), so it is
                # not settled paperwork and has to stay in the reviewer's list.
                {"start": 3, "end": 3, "category": "100", "title": "History and Physical"},
            ]
        },
    )
    assert saved.status_code == 200, saved.text

    rows = (await client.get(f"/api/documents/{doc_id}")).json()["rows"]
    assert [row["ruled_paperwork"] for row in rows] == [True, False, False]


async def test_rows_round_trip_with_the_rule_verdict_attached(authed):
    """The read-only field must survive a save: the client's stripKeys removes only `_key`, so
    every autosave echoes `ruled_paperwork` back to the server."""
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    saved = await client.put(
        f"/api/documents/{doc_id}/rows",
        json={
            "rows": [
                {
                    "start": 1,
                    "end": 2,
                    "category": _VALID_CATEGORY,
                    "title": "Placeholder Report",
                    "ruled_paperwork": False,
                }
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["count"] == 1

    rows = (await client.get(f"/api/documents/{doc_id}")).json()["rows"]
    assert len(rows) == 1
    assert rows[0]["title"] == "Placeholder Report"


def _set_method(doc_id, value, start=1, end=2):
    """Stamp a stored method onto one row, as the classifier would have."""
    with get_sessionmaker()() as session:
        row = session.scalar(
            select(ReviewRow).where(
                ReviewRow.document_id == doc_id, ReviewRow.start == start, ReviewRow.end == end
            )
        )
        row.method = value
        session.commit()


def _methods(doc_id):
    with get_sessionmaker()() as session:
        rows = session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == doc_id).order_by(ReviewRow.idx)
        ).all()
        return [row.method for row in rows]


async def test_an_autosave_preserves_the_stored_classification_method(authed):
    """WHEN rows are saved with their ranges unchanged, THE SYSTEM SHALL keep each row's method.

    `_store_rows` DELETES every row and rebuilds it from the client payload, so anything the server
    owns is destroyed by an ordinary autosave unless it is carried across explicitly - which is why
    the dedup fields have been preserved by page range since they were added. `method` is written by
    the classifier and cannot be recovered without re-running it at model cost, so losing it on the
    reviewer's first keystroke would quietly empty the column the #188 filter reads.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": "100", "title": "Placeholder Report"},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY, "title": "Office Visit Note"},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_method(doc_id, "llm-disagree", 1, 2)
    _set_method(doc_id, "rules", 3, 4)

    # An ordinary autosave: same ranges, one edited title.
    rows[0]["title"] = "Placeholder Report (edited)"
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200

    assert _methods(doc_id) == ["llm-disagree", "rules"]


async def test_changing_a_rows_page_range_clears_its_method(authed):
    """A row whose range moved is different content, so the old verdict no longer describes it.

    Same rule the dedup fields follow, and for the same reason: the method was decided from THESE
    pages. Carrying it onto a re-spanned row would attach a classification to text that was never
    classified.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [{"start": 1, "end": 2, "category": "100", "title": "Placeholder Report"}]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_method(doc_id, "llm-disagree", 1, 2)

    rows[0]["end"] = 3  # the reviewer widened the document
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200

    assert _methods(doc_id) == [None]


async def test_a_client_cannot_write_the_classification_method(authed):
    """`method` is server-owned. The payload round-trips it, so it must be IGNORED on the way in.

    Otherwise a client could mark any row as confidently-classified and remove it from the very list
    the reviewers asked for.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    rows = [{"start": 1, "end": 2, "category": "100", "title": "Placeholder Report"}]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_method(doc_id, "llm-disagree", 1, 2)

    rows[0]["method"] = "llm+embedding"  # a client claiming the row is settled paperwork
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200

    assert _methods(doc_id) == ["llm-disagree"]


async def test_rows_carry_the_stored_method_in_the_payload(authed):
    """The editor needs the stored verdict to narrow its filter, so the GET must return it."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": "100", "title": "Placeholder Report"},
        {"start": 3, "end": 4, "category": "100", "title": "Sample Report"},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_method(doc_id, "llm+embedding", 1, 2)

    payload = (await client.get(f"/api/documents/{doc_id}")).json()["rows"]
    # NULL for the untouched row: every row segmented before this column existed reads the same way,
    # and the filter shows those unchanged.
    assert [row["method"] for row in payload] == ["llm+embedding", None]


async def _rows_ready(client, doc_id, pages=4):
    """Two included rows, so summarize is not blocked by the "nothing selected" check."""
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY, "title": "A"},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY, "title": "B"},
    ]
    resp = await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    assert resp.status_code == 200, resp.text
    return rows


def _completed_dedup(doc_id, with_text=True):
    """A finished dedup job, plus the source_text such a run always leaves on every in-scope row."""
    with get_sessionmaker()() as session:
        session.add(
            Job(
                document_id=doc_id,
                kind="dedup",
                state="done",
                stage="deduping",
                current=1,
                total=1,
                model="m",
                title_model="m",
                audit_model="m",
                prompt_version="1",
                prompt_fingerprint="1",
                build_sha="test",
                catalog_revision=0,
            )
        )
        if with_text:
            for row in session.scalars(
                select(ReviewRow).where(ReviewRow.document_id == doc_id)
            ).all():
                row.source_text = "text"
        session.commit()


async def test_summarize_is_refused_when_no_duplicate_check_has_run(authed):
    """WHEN no dedup has ever completed, THE SYSTEM SHALL refuse to summarize.

    The defect this closes (#125): a record could go upload -> segment -> summarize with the
    duplicate check never running, and nothing said so - measured at 14 of 44 summarized documents
    on the box. The pipeline reported done, so duplicate copies reached a deliverable unseen.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    await _rows_ready(client, doc_id)

    resp = await client.post(f"/api/documents/{doc_id}/summarize/start", json={})
    assert resp.status_code == 409
    assert "not been checked for duplicates" in resp.json()["detail"]
    with get_sessionmaker()() as session:
        assert not session.scalars(
            select(Job).where(Job.document_id == doc_id, Job.kind == "summarize")
        ).all(), "nothing may be enqueued when the gate refuses"


async def test_summarize_is_refused_when_the_check_no_longer_covers_the_rows(authed):
    """A completed check whose coverage has moved is not a check of THESE rows.

    A completed dedup leaves source_text on every in-scope row, so a row without it means a
    boundary changed, a row appeared, or a row was newly included since. The message says which of
    the two refusals this is, because the reviewer's next action differs.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    await _rows_ready(client, doc_id)
    _completed_dedup(doc_id, with_text=False)  # ran, but covers none of the current rows

    resp = await client.post(f"/api/documents/{doc_id}/summarize/start", json={})
    assert resp.status_code == 409
    assert "changed since the last duplicate check" in resp.json()["detail"]


async def test_summarize_proceeds_once_a_current_check_exists(authed):
    """The ordinary path: check first, then summarize, with no flag and no audit needed."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    await _rows_ready(client, doc_id)
    _completed_dedup(doc_id)

    resp = await client.post(f"/api/documents/{doc_id}/summarize/start", json={})
    assert resp.status_code == 200, resp.text


async def test_a_reviewer_can_skip_the_check_and_the_choice_is_recorded(authed):
    """The gate is SOFT by design: skipping is allowed, but it must be a decision with a trace.

    Without the audit row this is indistinguishable from the omission the gate exists to stop.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    await _rows_ready(client, doc_id)

    resp = await client.post(
        f"/api/documents/{doc_id}/summarize/start", json={"skip_duplicate_check": True}
    )
    assert resp.status_code == 200, resp.text
    with get_sessionmaker()() as session:
        actions = [
            a.action
            for a in session.scalars(select(AuditLog).where(AuditLog.document_id == doc_id)).all()
        ]
    assert "summarize.skip_duplicate_check" in actions


async def test_an_invalid_row_is_reported_before_the_duplicate_gate(authed):
    """Order matters: a reviewer cannot act on the duplicate gate while their rows are invalid."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    resp = await client.post(
        f"/api/documents/{doc_id}/summarize/start",
        json={"rows": [{"start": 9, "end": 2, "category": _VALID_CATEGORY}]},
    )
    assert resp.status_code == 400
    assert "duplicate" not in resp.json()["detail"].lower()


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


async def test_duplicates_stale_flag_ignores_rows_the_reviewer_excluded(authed):
    """Staleness follows the dedup SCOPE, which is the rows the reviewer CHECKED.

    Rewritten 2026-08-06 with the scope change; it previously asserted the opposite, because dedup
    read every row. Without this the flag would be permanently true on any record with an exclusion:
    an excluded row is never OCR'd, so its source_text stays None forever and the tab would offer a
    re-check that could not change anything.

    A row the reviewer LATER includes is a different matter - dedup has genuinely not seen it, so the
    offer is real.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY, "include": False},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_dedup_fields(
        doc_id, [(1, 2)]
    )  # only the included row was checked, which is the whole scope
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

    # Every INCLUDED row has stored text; the excluded one never will. Not stale.
    assert await stale() is False

    # Including that row puts it in scope, and dedup has not read it -> the re-check offer is real.
    included = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY, "include": True},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": included})
    ).status_code == 200
    assert await stale() is True


async def test_duplicates_stale_flag_still_counts_a_dismissed_but_included_row(authed):
    """A DISMISSED cluster is not an EXCLUDED row - dismissing says "not duplicates", not "do not
    summarize" - so a dismissed row that is still checked stays in dedup's scope and still counts."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    rows = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY},
    ]
    assert (
        await client.put(f"/api/documents/{doc_id}/rows", json={"rows": rows})
    ).status_code == 200
    _set_dedup_fields(doc_id, [(1, 2)])  # row 2 has no stored text
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
        row = session.scalar(
            select(ReviewRow).where(ReviewRow.document_id == doc_id, ReviewRow.start == 3)
        )
        row.dupe_dismissed = True
        session.commit()

    resp = await client.get(f"/api/documents/{doc_id}/duplicates")
    assert resp.json()["stale"] is True


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
    # real, clickable hotspot
    assert link["from"].width > 1
    assert link["from"].height > 1
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
    assert "MRI Report" in word["summaryTitle"]
    assert "MRI Report" in pdf["linkTitle"]


# One decorated title, one date, one body, shared by the three tests below. `summarize_row` returns
# titles in this shape because the app displays all three markers; every path that produces a
# DELIVERED document has to take them off again.
_DECORATED_TITLE = "[ManualCheck] MRI OF THE CERVICAL SPINE [Diagnostic Study] (Pages 12-19)"
_PRESENTABLE_TITLE = "MRI OF THE CERVICAL SPINE"
_ENTRY_DATE = "01/02/2020"
_ENTRY_BODY = "body text"
_BUNDLE_ROW = {"start": 12, "end": 19, "category": "3", "flag": "x"}


def _stub_decorated_summarize_row(monkeypatch):
    """Make `bundles`' summarize_row return the decorated shape, and hand back the bundle entry."""
    from app.services import bundles

    monkeypatch.setattr(
        bundles.summarize_engine,
        "summarize_row",
        lambda *a, **k: {
            "summaryDate": _ENTRY_DATE,
            "summaryTitle": _DECORATED_TITLE,
            "summaryText": _ENTRY_BODY,
        },
    )
    return bundles.bundle_summary_entries("/x.pdf", [_BUNDLE_ROW])


def test_the_bundle_export_strips_the_same_markers_as_the_review_export(monkeypatch):
    """The THIRD path into a delivered Word document, and it was the one that did not strip.

    `bundles.bundle_summary_entries` built its entries straight from `summarize_row`'s decorated
    `summaryTitle`, so a bundle-summarize download shipped `[ManualCheck] `, ` [Diagnostic Study]`
    and ` (Pages X-Y)` to the client while the review export stripped all three. The test above
    pinned the invariant for the Word and linked-PDF paths - two of the three - which is how the
    asymmetry survived.
    """
    entries = _stub_decorated_summarize_row(monkeypatch)

    assert len(entries) == 1
    title = entries[0]["summaryTitle"]
    for marker in ("[ManualCheck]", "[Diagnostic Study]", "(Pages"):
        assert marker not in title, f"the bundle export still ships {marker}: {title!r}"
    assert title == _PRESENTABLE_TITLE


def test_one_blank_document_does_not_discard_the_rest_of_the_bundle(monkeypatch):
    """DEMONSTRATES the bug: an unreadable row threw away every summary generated before it.

    `summarize_row` raises EmptyExtractionError for a row whose pages read cleanly and yield no
    words - a photograph, a film, a separator sheet. With no per-row isolation that propagated out
    of `bundle_summary_entries`, and the caller's `except PipelineError` discarded the `entries`
    list, throwing away real model calls already spent and failing a bundle that was mostly fine.
    `_pipeline_error_response` already classifies that exception 422, "a property of the document".
    """
    from app.errors import EmptyExtractionError
    from app.services import bundles

    rows = [
        {"start": 1, "end": 2, "category": "3", "flag": "-"},
        {"start": 3, "end": 3, "category": "3", "flag": "-"},  # the blank one
        {"start": 4, "end": 5, "category": "3", "flag": "-"},
    ]

    def fake(pdf_path, row, *a, **k):
        if int(row["start"]) == 3:
            raise EmptyExtractionError("no OCR text for pages 3-3")
        return {"summaryDate": _ENTRY_DATE, "summaryTitle": _DECORATED_TITLE, "summaryText": "b"}

    monkeypatch.setattr(bundles.summarize_engine, "summarize_row", fake)

    entries = bundles.bundle_summary_entries("/x.pdf", rows)

    # The two readable documents survive; the blank one is omitted rather than sinking the bundle.
    assert len(entries) == 2


def test_a_missing_ocr_binary_still_aborts_the_whole_bundle(monkeypatch):
    """GUARDS the carve-out, and it is the half that makes the fix safe.

    OcrUnavailableError means Tesseract or Poppler is absent, which fails identically on every
    remaining row - continuing would spend the rest of the loop rediscovering that one row at a
    time and hand back a bundle silently missing everything. Only the per-document failure is
    skipped.
    """
    from app.errors import OcrUnavailableError
    from app.services import bundles

    def fake(pdf_path, row, *a, **k):
        raise OcrUnavailableError("Tesseract not found")

    monkeypatch.setattr(bundles.summarize_engine, "summarize_row", fake)

    with pytest.raises(OcrUnavailableError):
        bundles.bundle_summary_entries("/x.pdf", [_BUNDLE_ROW])


def test_all_three_export_paths_agree_on_the_same_decorated_title(monkeypatch):
    """Asserts the paths AGREE rather than checking each alone.

    Each was internally consistent and they still disagreed - the same shape as the Word/PDF
    formatting split. A one-sided test catches none of this.
    """
    from app.api.documents import _export_entry, _pdf_entry

    summary = Summary(
        document_id="d",
        job_id=1,
        idx=0,
        title=_DECORATED_TITLE,
        text=_ENTRY_BODY,
        row_start=_BUNDLE_ROW["start"],
        row_end=_BUNDLE_ROW["end"],
        row_category=_BUNDLE_ROW["category"],
        manual_check=True,
        date=_ENTRY_DATE,
    )
    bundle_title = _stub_decorated_summarize_row(monkeypatch)[0]["summaryTitle"]

    assert _export_entry(summary)["summaryTitle"] == bundle_title
    assert _pdf_entry(summary)["linkTitle"] == bundle_title


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


async def test_a_bundle_ships_only_the_documents_the_reviewer_is_shipping(authed):
    """DEMONSTRATES the bug: an unchecked row was bundled anyway.

    A bundle is a deliverable - a combined PDF or a Word report a client receives - and this path
    selected purely on category, straight off the table. The single-record export does not: it drops
    `summary.excluded` first. So the two delivery paths disagreed about what the reviewer had
    decided to ship.

    The reachable case is `resolve_duplicate`'s keep_one, which sets `member.include = is_primary
    and wanted` and leaves the CATEGORY alone - so a confirmed duplicate copy stayed in category 3
    and its pages went into the bundle PDF a second time.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=3)
    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={
            "rows": [
                {"start": 1, "end": 1, "category": _VALID_CATEGORY, "include": True},
                # The shape keep_one leaves behind: same category, unchecked.
                {"start": 2, "end": 2, "category": _VALID_CATEGORY, "include": False},
                {"start": 3, "end": 3, "category": _VALID_CATEGORY, "include": True},
            ]
        },
    )

    got = await client.post(
        f"/api/documents/{doc_id}/bundle/pdf", json={"categories": [_VALID_CATEGORY]}
    )

    from pypdf import PdfReader

    assert got.status_code == 200
    # Two included pages, not three: the excluded row's page is absent from the delivered PDF.
    assert len(PdfReader(io.BytesIO(got.content)).pages) == 2


async def test_a_bundle_whose_every_match_is_excluded_says_so_rather_than_none(authed):
    """The two 409s are different situations and a reviewer reads them differently.

    "no matching documents in this record" sent someone looking for documents that are right there
    on screen in the right category - they had just unchecked them. Worth its own message.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={
            "rows": [
                {"start": 1, "end": 1, "category": _VALID_CATEGORY, "include": False},
                {"start": 2, "end": 2, "category": _VALID_CATEGORY, "include": False},
            ]
        },
    )

    got = await client.post(
        f"/api/documents/{doc_id}/bundle/pdf", json={"categories": [_VALID_CATEGORY]}
    )

    assert got.status_code == 409
    detail = got.json()["detail"]
    assert "excluded" in detail
    assert "2" in detail
    assert "no matching documents" not in detail


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
        assert (
            session.scalar(select(Summary.verified_title).where(Summary.document_id == doc_id))
            is None
        )


# The five provenance columns `_build_summary` sets and resummarize did not. The docstring above
# claims verified_title was "the only one of the fifteen fields _build_summary sets that resummarize
# missed" - that claim was itself wrong, and `model` is the one that costs content rather than
# attribution.
_FALLBACK_MODEL = "gemini-3.5-flash"
_CONFIGURED_MODEL = "gemini-2.5-pro"


async def test_a_re_draft_that_can_read_nothing_stays_retryable(authed, monkeypatch):
    """WHEN a re-draft comes back as a notice-only row, THE SYSTEM SHALL leave `model` NULL.

    `_unreadable_output` returns `model=None` deliberately - it is what distinguishes a notice-only
    row from one summarized off its readable pages - and `_is_retryable_notice` requires
    `summary.model is None` before it will re-attempt the row. Leaving the PREVIOUS draft's model in
    place produced the one combination the retry logic cannot recognise, so a row that OCR'd cleanly
    first time and hit a transient extraction failure on re-draft became permanently un-retryable:
    the next Summarize reuses it and the pages are never re-read.
    """
    from app.worker.tasks import _is_retryable_notice

    client, _ = authed
    doc_id = await _upload(client, pages=1)
    _seed_summary(doc_id)
    with get_sessionmaker()() as session:  # the first draft was answered by a real model
        summary = session.scalar(select(Summary).where(Summary.document_id == doc_id))
        summary.model = _CONFIGURED_MODEL
        session.commit()

    import app.api.documents as documents_module

    monkeypatch.setattr(
        documents_module,
        "summarize_row",
        _fake_summarize(model=None, unreadablePages=[1], sourceText=None),
    )
    resp = await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")

    assert resp.status_code == 200
    with get_sessionmaker()() as session:
        summary = session.scalar(select(Summary).where(Summary.document_id == doc_id))
        assert summary.unreadable is True
        assert summary.model is None, (
            "a notice-only re-draft must clear `model`, or the row can never be retried"
        )
        assert _is_retryable_notice(summary), "the next Summarize would skip this row forever"


async def test_a_re_draft_answered_by_the_fallback_model_is_flagged_and_attributed(
    authed, monkeypatch
):
    """WHEN the body was answered by the fallback after the configured model refused, THE SYSTEM
    SHALL flag the row and record which model actually answered.

    Both are how the downgrade surfaces at all: the worker path does exactly this, and the measured
    gap between the two models on a long row is 6 of 18 required points versus 16.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    _seed_summary(doc_id)

    import app.api.documents as documents_module

    monkeypatch.setattr(
        documents_module,
        "summarize_row",
        _fake_summarize(model=_FALLBACK_MODEL, bodyFallbackFrom=_CONFIGURED_MODEL),
    )
    resp = await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")

    assert resp.status_code == 200
    with get_sessionmaker()() as session:
        summary = session.scalar(select(Summary).where(Summary.document_id == doc_id))
        assert summary.manual_check is True, "a fallback-answered re-draft must be flagged"
        assert summary.model == _FALLBACK_MODEL, "the model that ANSWERED must be recorded"


async def test_a_re_draft_rewrites_every_provenance_column(authed, monkeypatch):
    """None of the five may survive from the previous draft - they would describe a body that no
    longer exists."""
    client, _ = authed
    doc_id = await _upload(client, pages=1)
    _seed_summary(doc_id)
    with get_sessionmaker()() as session:
        summary = session.scalar(select(Summary).where(Summary.document_id == doc_id))
        summary.model = "stale-body"
        summary.title_model = "stale-title"
        summary.audit_model = "stale-audit"
        summary.prompt_fingerprint = "stalefp1"
        summary.audit_fingerprint = "stalefp2"
        session.commit()

    import app.api.documents as documents_module

    monkeypatch.setattr(
        documents_module,
        "summarize_row",
        _fake_summarize(
            model="fresh-body",
            titleModel="fresh-title",
            auditModel="fresh-audit",
            promptFingerprint="freshfp1",
            auditFingerprint="freshfp2",
        ),
    )
    resp = await client.post(f"/api/documents/{doc_id}/summaries/0/resummarize")

    assert resp.status_code == 200
    with get_sessionmaker()() as session:
        summary = session.scalar(select(Summary).where(Summary.document_id == doc_id))
        assert summary.model == "fresh-body"
        assert summary.title_model == "fresh-title"
        assert summary.audit_model == "fresh-audit"
        assert summary.prompt_fingerprint == "freshfp1"
        assert summary.audit_fingerprint == "freshfp2"


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
        assert (
            session.scalar(select(ReviewRow.category).where(ReviewRow.document_id == doc_id))
            == _OTHER_CATEGORY
        )
        assert (
            session.scalar(select(Summary.row_category).where(Summary.document_id == doc_id))
            == _VALID_CATEGORY
        )  # snapshot untouched
        entry = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "summary.category", AuditLog.document_id == doc_id
            )
        )
        assert entry is not None
        assert _VALID_CATEGORY in entry.detail
        assert _OTHER_CATEGORY in entry.detail


async def test_a_row_save_records_the_boundary_work(authed):
    """WHEN a reviewer saves a changed row set, THE SYSTEM SHALL record rows before and after and
    the boundaries removed and added.

    Counted on boundary SETS rather than by pairing rows, because a merge renumbers every row after
    it - pairing by index would report the whole tail as changed.
    """
    from app.models import AuditLog

    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, None), (3, 4, None), (5, 6, None)])

    merged = [
        {"start": 1, "end": 4, "category": _VALID_CATEGORY},
        {"start": 5, "end": 6, "category": _VALID_CATEGORY},
    ]
    resp = await client.put(f"/api/documents/{doc_id}/rows", json={"rows": merged})

    assert resp.status_code == 200, resp.text
    with get_sessionmaker()() as session:
        entry = session.scalar(
            select(AuditLog).where(AuditLog.action == "rows.edit", AuditLog.document_id == doc_id)
        )
        assert entry is not None
        assert entry.detail == "rows 3->2 (merges 1, splits 0, pages 6->6)"


async def test_a_row_save_that_changes_nothing_is_still_recorded(authed):
    """WHEN a reviewer saves a row set identical to the stored one, THE SYSTEM SHALL still record
    the event with zero counts.

    "Opened it and confirmed it" is a different fact from "nobody has looked at it", and the rows
    carry no timestamp of their own - _store_rows recreates them - so only the event separates the
    two.
    """
    from app.models import AuditLog

    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, None), (3, 4, None)])

    same = [
        {"start": 1, "end": 2, "category": _VALID_CATEGORY},
        {"start": 3, "end": 4, "category": _VALID_CATEGORY},
    ]
    resp = await client.put(f"/api/documents/{doc_id}/rows", json={"rows": same})

    assert resp.status_code == 200, resp.text
    with get_sessionmaker()() as session:
        entry = session.scalar(
            select(AuditLog).where(AuditLog.action == "rows.edit", AuditLog.document_id == doc_id)
        )
        assert entry is not None
        assert entry.detail == "rows 2->2 (merges 0, splits 0, pages 4->4)"


async def test_a_summary_edit_is_recorded_and_stamps_the_row(authed):
    """WHEN a reviewer edits a summary field, THE SYSTEM SHALL record which fields changed and set
    updated_at."""
    from app.models import AuditLog

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))  # text is "old text", 8 characters

    resp = await client.put(
        f"/api/documents/{doc_id}/summaries/0", json={"summaryText": "a corrected body"}
    )

    assert resp.status_code == 200, resp.text
    with get_sessionmaker()() as session:
        entry = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "summary.edit", AuditLog.document_id == doc_id
            )
        )
        assert entry is not None
        assert "edited_text" in entry.detail
        assert "body chars +8" in entry.detail  # 16 - 8
        assert session.scalar(select(Summary.updated_at).where(Summary.document_id == doc_id))


async def test_no_edit_audit_detail_carries_document_text(authed):
    """THE SYSTEM SHALL NOT write a title, body, date or filename into audit_log.detail.

    The column is read by humans and is not covered by the upload-and-delete lifecycle, so anything
    landing in it is PHI that persists indefinitely. A character COUNT is a length, not content,
    which is why the summary event carries a delta rather than the text.
    """
    from app.models import AuditLog

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])  # seeds title "Doc 0", date "01/01/2026"
    _seed_summary(doc_id, pages=(1, 2))

    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 2, "category": _VALID_CATEGORY, "title": "Doc 0"}]},
    )
    await client.put(
        f"/api/documents/{doc_id}/summaries/0",
        json={"summaryTitle": "a patient name", "summaryText": "a clinical finding"},
    )

    with get_sessionmaker()() as session:
        details = session.scalars(
            select(AuditLog.detail).where(
                AuditLog.document_id == doc_id,
                AuditLog.action.in_(("rows.edit", "summary.edit")),
            )
        ).all()
        assert details, "expected both edit events"
        for detail in details:
            for leak in ("Doc 0", "01/01/2026", "a patient name", "a clinical finding", "scan.pdf"):
                assert leak not in detail, f"{leak!r} reached audit_log.detail"


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
        assert (
            session.scalar(select(ReviewRow.category).where(ReviewRow.document_id == doc_id))
            == _VALID_CATEGORY
        )


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
        assert (
            session.scalar(select(ReviewRow.category).where(ReviewRow.document_id == doc_id))
            == _VALID_CATEGORY
        )


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
        # Same invariant, same reason: the client replaces the cached item wholesale, so a route
        # answering with the bare listing() would delete the stranded-summary badge from the cache
        # after a save - the exact failure this test was written for, on the newer field.
        assert "rowMissing" in body, f"{name} dropped rowMissing"
        assert "rowMethodLive" in body, f"{name} dropped rowMethodLive"
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
            assert entry is not None
            assert "force False" in entry.detail
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
            entry = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "job.cancel", AuditLog.document_id == doc_id
                )
            )
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
        assert (
            session.scalar(select(ReviewRow.source_text).where(ReviewRow.document_id == doc_id))
            == "previous extraction"
        )


async def test_segment_start_enqueues_then_conflicts(authed):
    """P4b: segment/start enqueues a job; a second start while it's active returns 409."""
    from tests.conftest import lanes

    client, _ = authed
    doc_id = await _upload(client, pages=2)
    queue = lanes("segment")
    queue.empty()
    try:
        first = await client.post(f"/api/documents/{doc_id}/segment/start")
        assert first.status_code == 200
        assert first.json() == {"ok": True}
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
    # skip_duplicate_check because these rows are CREATED by this very call, so no earlier
    # duplicate check could cover them and the #125 gate would refuse. The gate's own behaviour
    # is covered by the four tests above; this one is about the row flush.
    try:
        resp = await client.post(
            f"/api/documents/{doc_id}/summarize/start",
            json={
                "rows": [{"start": 1, "end": 1, "category": _VALID_CATEGORY}],
                "skip_duplicate_check": True,
            },
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
            json={
                "rows": [{"start": 1, "end": 1, "category": _VALID_CATEGORY}],
                "fresh": True,
                # See the note above: these rows are created by this call, so the #125 duplicate
                # gate has nothing that could cover them. This test is about `fresh`.
                "skip_duplicate_check": True,
            },
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
    assert got["patient_first_name"] == ""
    assert got["law_firm"] == ""


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
        assert body["page_count"] == 5
        assert len(body["records"]) == 2

        got = await client.get(f"/api/documents/{body['id']}")
        rows = got.json()["rows"]
        assert len(rows) == 2
        assert (rows[0]["start"], rows[0]["end"]) == (1, 2)
        assert (rows[1]["start"], rows[1]["end"]) == (3, 5)
        # General (100) seeds off-by-default (classify re-derives per row afterwards).
        assert rows[0]["include"] is False
        assert rows[1]["include"] is False

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


async def test_a_copy_declared_distinct_after_keep_one_returns_to_the_report(authed):
    """WHEN a reviewer keeps one copy and THEN declares another a distinct document, THE SYSTEM SHALL
    put that row back in the report.

    `keep_one` sets `include = is_primary and wanted`, so every non-primary copy is left unchecked -
    correct while they ARE copies. "Not a duplicate" then cleared the group and never touched
    `include`, and a row with no `dupe_group` is invisible to `_dupe_groups`. So the copy vanished from
    the Duplicates tab while staying excluded from the report: the cluster reads "Resolved", nothing on
    that surface mentions the row, and the only trace is an unchecked box on an unrelated-looking row
    in the editor.

    `test_duplicates_remove_member_leaves_the_rest_grouped` asserts the removed copy stays included,
    but its fixture seeds `include=True` and never runs keep_one first, so the ORDERING that breaks it
    was untested. Both buttons are live on the same card at once - "Not a duplicate" is gated only on
    the cluster not being dismissed and on `busy`, so it stays enabled on a card already showing
    "Resolved".
    """
    client, _ = authed
    doc_id = await _upload(client, pages=8)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1), (5, 6, 1)])

    kept = await client.post(
        f"/api/documents/{doc_id}/duplicates/1/resolve",
        json={"action": "keep_one", "primary_idx": 0},
    )
    assert kept.status_code == 200
    with get_sessionmaker()() as session:
        rows = {
            r.idx: r
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        }
        assert rows[2].include is False, "premise: keep_one leaves the non-primary copies excluded"

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
    assert rows[2].include is True, (
        "a row declared a distinct document is invisible in the Duplicates tab, so leaving it "
        "excluded drops its pages from the report with nothing on screen to say so"
    )
    # The surviving pair is untouched: still one cluster, still resolved to the kept copy.
    assert rows[0].dupe_group == 1
    assert rows[1].dupe_group == 1
    assert rows[0].include is True
    assert rows[1].include is False


async def test_the_last_copy_of_a_collapsed_cluster_also_returns_to_the_report(authed):
    """Removing a member from a PAIR collapses the cluster, and the row left behind has its group
    cleared too - so it needs the same restoration. If it was the non-primary of an earlier keep_one
    it would otherwise be both groupless and excluded."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1)])

    await client.post(
        f"/api/documents/{doc_id}/duplicates/1/resolve",
        json={"action": "keep_one", "primary_idx": 0},
    )
    removed = await client.post(
        f"/api/documents/{doc_id}/duplicates/1/resolve",
        json={"action": "remove_member", "idx": 0},
    )
    assert removed.status_code == 200

    with get_sessionmaker()() as session:
        rows = {
            r.idx: r
            for r in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc_id)).all()
        }
    assert all(r.dupe_group is None for r in rows.values()), (
        "a cluster of one is not a duplicate set"
    )
    assert rows[1].include is True, (
        "the row left behind must not stay excluded once it has no cluster"
    )


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
    assert rows[0].dupe_primary is True
    assert rows[0].include is True
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
    # but nothing was opted in
    assert rows[0].include is False
    assert rows[1].include is False
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
    assert started.status_code == 200
    assert started.json()["ok"] is True


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
    assert dup.status_code == 200
    assert dup.json()["job"] is not None  # progress surfaced

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
    assert rows[0].dupe_group == 1
    assert rows[1].dupe_group == 1
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


async def test_duplicates_say_whether_a_check_has_ever_completed(authed):
    """WHEN no duplicate check has completed, THE SYSTEM SHALL NOT present the record as clean.

    Empty `clusters` means two different things and the payload carried no way to tell them apart: a
    completed run that found nothing, and no run at all. The second is the DEFAULT state of every
    record, because dedup only runs when someone asks - so the tab was reporting "No duplicate
    documents found" on records nothing had looked at. Measured 2026-08-19 across four records taken
    end to end, two of whose human deliverables count 6 and 2 pages of duplicate copies.

    `stale` and `unreadable` cannot stand in for this: both are themselves gated on a completed dedup
    job, so on a never-checked document all three of clusters/stale/unreadable are empty or falsy.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, None), (3, 4, None)])

    body = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert body["checked"] is False, "no dedup job has ever run on this document"
    # The three fields that previously had to carry this meaning, and could not.
    assert body["clusters"] == []
    assert body["stale"] is False
    assert body["unreadable"] == 0

    _finish_dedup(doc_id)
    body = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert body["checked"] is True, "a completed run reports as checked even with no clusters"
    assert body["clusters"] == []


def test_dupe_date_key_parses_dates_and_defaults_unknown():
    from app.api.documents import _dupe_date_key

    assert _dupe_date_key("03/10/2026") == (2026, 3, 10)
    assert _dupe_date_key("1/2/26") == (2026, 1, 2)  # 2-digit year -> 2000s
    assert _dupe_date_key("-") == (9999, 12, 31)  # unknown sorts last
    assert _dupe_date_key("") == (9999, 12, 31)


# --- C9 (T4): the notice reaches BOTH deliverables through the ordinary Summary render path --------


def _notice_summary(**over):
    """A delivered notice row. No model, no source text and no verify pass - the three things an
    export path could reasonably assume exist, which is why T4 asserts rather than assumes."""
    from app.services.summarize_engine import unreadable_notice

    fields = dict(
        document_id="d",
        job_id=1,
        idx=0,
        title="MRI OF THE KNEE (Pages 3-5)",
        text=unreadable_notice([4]),
        row_start=3,
        row_end=5,
        row_category="3",
        date="2026-03-04",
        unreadable=True,
        model=None,
        source_text=None,
        verified=False,
        verified_text=None,
        verified_title=None,
    )
    fields.update(over)
    return Summary(**fields)


def test_both_exports_render_an_unreadable_notice_with_no_export_side_change():
    """The notice reaches the deliverable through the SAME path as every other entry, because it is
    stored as a real Summary rather than synthesized at export time."""
    from app.api.documents import _export_entry, _pdf_entry
    from app.services.summarize_engine import unreadable_notice

    summary = _notice_summary()
    word = _export_entry(summary)
    pdf = _pdf_entry(summary)

    assert word["summaryText"] == unreadable_notice([4])
    assert pdf["summaryText"] == unreadable_notice([4])
    assert "unintelligible" in word["summaryText"].lower()
    assert "page 4" in word["summaryText"]
    # The entry keeps its identity: its title, its date, and a working link target.
    assert "MRI OF THE KNEE" in word["summaryTitle"]
    assert "MRI OF THE KNEE" in pdf["linkTitle"]
    assert word["summaryDate"] == "2026-03-04"
    assert pdf["startPage"] == 3


def test_an_untitled_notice_entry_still_names_its_pages_after_the_suffix_strip():
    """The page range has to survive into the deliverable. Every export strips a trailing
    "(Pages X-Y)", so an untitled notice row carries the label in its title PROPER - otherwise the
    entry would reach the client identified by nothing at all."""
    from app.api.documents import _export_entry, _pdf_entry

    summary = _notice_summary(title="Pages 5-6", row_start=5, row_end=6)

    assert _export_entry(summary)["summaryTitle"] == "Pages 5-6"
    assert _pdf_entry(summary)["linkTitle"] == "Pages 5-6"
    assert _pdf_entry(summary)["startPage"] == 5


def test_a_notice_entry_gains_no_fabricated_doi_prefix():
    """Every summarized entry opens with "**DOI**: ..." and a notice deliberately does not - the
    prefix qualifies summary content, and there is none. _export_title_and_text re-adds a DOI only
    when the STORED body already carries one, so this must come through clean rather than as a bare
    "**DOI**:" with nothing after it."""
    from app.api.documents import _export_entry

    assert "**DOI**" not in _export_entry(_notice_summary())["summaryText"]


def test_a_reviewer_edit_still_wins_over_a_notice():
    """effective_text precedence is untouched: a reviewer who reads the page themselves and writes the
    entry by hand is not overwritten by the notice."""
    from app.api.documents import _export_entry

    summary = _notice_summary(edited_text="Read by hand: lumbar MRI, disc protrusion at L4-L5.")
    text = _export_entry(summary)["summaryText"]
    assert "unintelligible" not in text.lower()
    assert "disc protrusion" in text


def test_a_partial_notice_survives_both_exports():
    """The appended sentence is part of the stored body, so it needs no export-side handling either -
    and the DOI a partial row really does carry is still restored."""
    from app.api.documents import _export_entry, _pdf_entry
    from app.services.summarize_engine import partial_unreadable_notice

    tail = partial_unreadable_notice([4])
    summary = _notice_summary(
        text=f"**DOI**: 01/02/2026. Lumbar tenderness on palpation. {tail}",
        model="gemini-2.5-pro",
    )

    assert _export_entry(summary)["summaryText"].endswith(tail)
    assert _pdf_entry(summary)["summaryText"].endswith(tail)
    assert "**DOI**: 01/02/2026." in _export_entry(summary)["summaryText"]


def _dedup_job(doc_id, state: str, error: str | None = None) -> None:
    """A dedup job in a terminal state, appended after any existing ones."""
    with get_sessionmaker()() as session:
        session.add(
            Job(
                document_id=doc_id,
                kind="dedup",
                state=state,
                stage="deduping",
                model="m",
                prompt_version="1",
                error=error,
            )
        )
        session.commit()


async def test_a_cancelled_recheck_does_not_erase_the_completed_check_before_it(authed):
    """DEMONSTRATES the bug. The derived flags read the newest dedup job of ANY state, but
    `dedup_document` rewrites the grouping in one transaction at the very end - deliberately, so a
    run that dies leaves the previous clusters intact. So after a cancelled re-check the stored
    clusters still came from the last COMPLETED run while `checked`, `stale` and `unreadable` all
    reported as though nothing had ever run.

    What the reviewer saw: "No duplicate check has run on this record yet" printed directly above
    the groups it was asking them to resolve.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=8)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1)])
    _finish_dedup(doc_id)

    before = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert before["checked"] is True
    assert len(before["clusters"]) == 1

    # The reviewer starts a re-check on a large record and presses Stop.
    _dedup_job(doc_id, "cancelled")

    after = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert after["clusters"] == before["clusters"], "premise: the stored clusters survive"
    assert after["checked"] is True, "a completed run happened, whatever the newest job did"
    # `job` still reports the NEWEST job, so the tab can say what just happened.
    assert after["job"]["state"] == "cancelled"


async def test_a_failed_recheck_still_reports_what_the_completed_run_found(authed):
    """The same for an errored re-check, and it carries the warning too: `unreadable` was zeroed, so
    the "N sub-documents could not be read and were not compared" notice disappeared - which is the
    exact wrong conclusion that count exists to prevent."""
    client, _ = authed
    doc_id = await _upload(client, pages=8)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1)])
    with get_sessionmaker()() as session:
        row = session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == doc_id, ReviewRow.idx == 1)
        ).first()
        row.source_text = "   "  # read, but no text recognized
        session.commit()
    _finish_dedup(doc_id)
    assert (await client.get(f"/api/documents/{doc_id}/duplicates")).json()["unreadable"] == 1

    _dedup_job(doc_id, "error", error="Vertex quota exhausted")

    body = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert body["checked"] is True
    assert body["unreadable"] == 1, "the completed run's warning must survive a failed re-check"
    assert body["job"]["state"] == "error"
    assert body["job"]["error"]


async def test_a_record_whose_only_dedup_failed_still_reports_as_unchecked(authed):
    """The guard must not turn every failure into "checked". With no completed run behind it, a
    failed job means nothing has been compared - and the tab has a separate banner for that."""
    client, _ = authed
    doc_id = await _upload(client, pages=8)
    _seed_rows(doc_id, [(1, 2, 1), (3, 4, 1)])
    _dedup_job(doc_id, "error", error="boom")

    body = (await client.get(f"/api/documents/{doc_id}/duplicates")).json()
    assert body["checked"] is False
    assert body["stale"] is False
    assert body["unreadable"] == 0
    assert body["job"]["state"] == "error"


async def test_the_listing_timestamps_carry_their_utc_offset(authed):
    """DEMONSTRATES the bug: on origin/main these come back with no Z and no offset.

    Every domain timestamp column is a bare `DateTime` (TIMESTAMP WITHOUT TIME ZONE), so a value
    read back can never carry tzinfo even though `_utcnow` wrote an aware UTC one. A bare
    `isoformat()` therefore produced "2026-08-27T01:00:00", and the ECMAScript date-time grammar
    reads that form as LOCAL time - so `new Date(doc.created_at)` shifted every timestamp by the
    viewer's offset.

    Measured in Pacific (UTC-7): the Uploaded column was a day late for anything uploaded after
    17:00 local, and Last activity read "Just now" for a record whose last job finished up to seven
    hours ago - so it could not distinguish a stalled record from a fresh one, which is what it is
    for.
    """
    from datetime import UTC, datetime

    client, _ = authed
    doc_id = await _upload(client, pages=2)

    body = (await client.get("/api/documents")).json()
    row = next(d for d in body if d["id"] == doc_id)

    for field in ("created_at", "updated_at"):
        value = row[field]
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, f"{field} has no offset, so a browser reads it as local"
        assert parsed.utcoffset().total_seconds() == 0, f"{field} is not UTC"
        # Within a few minutes of now, i.e. the instant is right as well as the marker.
        assert abs((datetime.now(UTC) - parsed).total_seconds()) < 300


async def test_the_audit_separates_a_deleted_sub_document_from_a_merge(authed):
    """WHEN a reviewer deletes a sub-document, THE SYSTEM SHALL record that its pages left the
    deliverable, rather than logging it identically to a merge.

    On row and boundary arithmetic alone the two acts are indistinguishable. Deleting [3-3] from
    1-2 / 3-3 / 4-5 drops end 3 and one row; merging [3-3] into [4-5] drops end 3 and one row. Both
    audited as `rows 3->2 (merges 1, splits 0)` - and `_store_rows` DELETES the row set rather than
    updating it, so nothing afterwards can tell them apart either.

    They are not the same act. A merge keeps every page in the deliverable; a delete takes those
    pages out of it, and nothing then covers them. Gaps are legal on purpose - reviewers skip junk
    pages - so this is not something to reject, it is something the trail has to be able to say.

    Found reconciling a 247-page record whose audit read as ten merges and whose saved rows had a
    one-page hole nothing accounted for.
    """
    from app.models import AuditLog

    client, _ = authed
    doc_id = await _upload(client, pages=5)
    _seed_rows(doc_id, [(1, 2, None), (3, 3, None), (4, 5, None)])

    # The reviewer deletes the middle sub-document: page 3 is now covered by nothing.
    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={
            "rows": [
                {"start": 1, "end": 2, "category": _VALID_CATEGORY},
                {"start": 4, "end": 5, "category": _VALID_CATEGORY},
            ]
        },
    )

    with get_sessionmaker()() as session:
        entry = session.scalar(
            select(AuditLog).where(AuditLog.action == "rows.edit", AuditLog.document_id == doc_id)
        )
    assert entry.detail == "rows 3->2 (merges 1, splits 0, pages 5->4)"


async def test_a_merge_of_the_same_shape_keeps_every_page(authed):
    """The other half of the pin, and the reason the page count is the right discriminator: a merge
    with IDENTICAL row and boundary arithmetic to the delete above leaves the coverage untouched."""
    from app.models import AuditLog

    client, _ = authed
    doc_id = await _upload(client, pages=5)
    _seed_rows(doc_id, [(1, 2, None), (3, 3, None), (4, 5, None)])

    # Same row count change, same dropped boundary - but [3-3] is merged into its neighbour.
    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={
            "rows": [
                {"start": 1, "end": 2, "category": _VALID_CATEGORY},
                {"start": 3, "end": 5, "category": _VALID_CATEGORY},
            ]
        },
    )

    with get_sessionmaker()() as session:
        entry = session.scalar(
            select(AuditLog).where(AuditLog.action == "rows.edit", AuditLog.document_id == doc_id)
        )
    assert entry.detail == "rows 3->2 (merges 1, splits 0, pages 5->5)"


async def test_shrinking_a_sub_document_is_recorded_even_though_no_row_is_lost(authed):
    """A reviewer who pulls a boundary in loses pages without losing a row, so `rows N->N` says
    nothing happened. The page count is the only field that reports it."""
    from app.models import AuditLog

    client, _ = authed
    doc_id = await _upload(client, pages=5)
    _seed_rows(doc_id, [(1, 5, None)])

    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 3, "category": _VALID_CATEGORY}]},
    )

    with get_sessionmaker()() as session:
        entry = session.scalar(
            select(AuditLog).where(AuditLog.action == "rows.edit", AuditLog.document_id == doc_id)
        )
    assert entry.detail == "rows 1->1 (merges 1, splits 1, pages 5->3)"


def test_covered_page_count_counts_pages_not_span_lengths():
    """Deliberately a set of page numbers rather than a sum of span lengths. The saved rows are
    validated non-overlapping so the two agree today; a sum would double-count if that ever
    changed, and this number's whole job is to be trustworthy in an audit trail."""
    from app.api.documents import _covered_page_count

    assert _covered_page_count([]) == 0
    assert _covered_page_count([(1, 1)]) == 1
    assert _covered_page_count([(1, 2), (4, 5)]) == 4  # the gap at 3 is not counted
    assert _covered_page_count([(1, 3), (2, 5)]) == 5  # overlap counted once, not 3 + 4


def _set_document_status(doc_id, status):
    with get_sessionmaker()() as session:
        session.get(Document, doc_id).status = status
        session.commit()


def _document_status(doc_id):
    with get_sessionmaker()() as session:
        return session.get(Document, doc_id).status


async def test_a_row_edit_that_strands_a_summary_reopens_a_finished_record(authed):
    """WHEN a reviewer merges sub-documents on a finished record, THE SYSTEM SHALL move it back to
    `reviewing`.

    A summary is bound to its row by the page range stored on it, and that binding is a snapshot.
    `put_rows` never touched `status`, so merging two sub-documents on a `done` record left the
    record reporting Done while its stored text described pages nothing claimed any more - and the
    export takes `document.summaries` filtered on `excluded` alone, so the client received the
    PRE-edit split with the reviewer's merge nowhere in it.

    Observed on the box 2026-08-28: a 247-page record marked `done` whose reviewer merged pages
    223-226 and 227-227 at 19:56, 49 minutes after the summarize run finished - leaving two
    summaries for rows that no longer exist and one row with no summary at all.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, None), (3, 4, None)])
    _seed_summary(doc_id, idx=0, pages=(1, 2))
    _seed_summary(doc_id, idx=1, pages=(3, 4))
    _set_document_status(doc_id, "done")

    merged = await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 4, "category": _VALID_CATEGORY}]},
    )

    assert merged.status_code == 200
    assert merged.json()["reopened"] is True
    assert _document_status(doc_id) == "reviewing"


async def test_needs_attention_reopens_the_same_way(authed):
    """`needs_attention` makes the same claim `done` does - the deliverable is built - so a row edit
    that strands a summary has to move it too, or the record keeps offering the old entry list."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, None), (3, 4, None)])
    _seed_summary(doc_id, idx=0, pages=(1, 2))
    _set_document_status(doc_id, "needs_attention")

    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 4, "category": _VALID_CATEGORY}]},
    )

    assert _document_status(doc_id) == "reviewing"


async def test_an_edit_that_leaves_every_summary_bound_keeps_the_record_finished(authed):
    """IF the saved rows still match every stored summary, THEN the record SHALL stay `done`.

    The demotion has to be driven by the summaries actually being stranded, not by the reviewer
    having saved at all - retitling or re-dating a finished record must not reopen it, and the
    editor autosaves the whole row set on every such edit.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, None), (3, 4, None)])
    _seed_summary(doc_id, idx=0, pages=(1, 2))
    _seed_summary(doc_id, idx=1, pages=(3, 4))
    _set_document_status(doc_id, "done")

    resaved = await client.put(
        f"/api/documents/{doc_id}/rows",
        json={
            "rows": [
                {"start": 1, "end": 2, "category": _VALID_CATEGORY, "title": "renamed"},
                {"start": 3, "end": 4, "category": _VALID_CATEGORY},
            ]
        },
    )

    assert resaved.json()["reopened"] is False
    assert _document_status(doc_id) == "done"


async def test_an_included_row_with_no_summary_also_reopens_the_record(authed):
    """A SPLIT strands nothing but leaves a new sub-document with no text, whose pages then reach no
    deliverable at all. Both halves of `stranded_summaries` have to demote, not just the orphan half
    - otherwise splitting a summarized row on a finished record silently drops content."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, idx=0, pages=(1, 2))
    _set_document_status(doc_id, "done")

    await client.put(
        f"/api/documents/{doc_id}/rows",
        json={
            "rows": [
                {"start": 1, "end": 2, "category": _VALID_CATEGORY},
                {"start": 3, "end": 4, "category": _VALID_CATEGORY},
            ]
        },
    )

    assert _document_status(doc_id) == "reviewing"


async def test_the_reopen_never_promotes_an_unfinished_record(authed):
    """IT SHALL only ever demote OUT of a finished stage. A mid-review record has stranded summaries
    constantly - that is what reviewing IS - and writing `reviewing` over `uploaded` would claim
    segmentation had produced rows it has not."""
    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, idx=0, pages=(1, 2))
    _set_document_status(doc_id, "uploaded")

    resp = await client.put(
        f"/api/documents/{doc_id}/rows",
        json={"rows": [{"start": 1, "end": 4, "category": _VALID_CATEGORY}]},
    )

    assert resp.json()["reopened"] is False
    assert _document_status(doc_id) == "uploaded"


async def test_get_summaries_reports_a_summary_whose_row_is_gone(authed):
    """WHEN no sub-document covers a summary's stored pages, THE SYSTEM SHALL say so positively.

    `rowCategoryLive` already goes null here, but the client deliberately coalesces null with
    undefined so an older backend cannot flag every card during a rolling deploy - which left this
    failure with no signal at all. A separate boolean gets both: absent reads falsy, True is real.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=3)
    _seed_rows(doc_id, [(1, 1, None)])
    _seed_summary(doc_id, pages=(2, 3))

    body = (await client.get(f"/api/documents/{doc_id}/summaries")).json()

    assert body[0]["rowMissing"] is True
    assert body[0]["rowCategoryLive"] is None


async def test_get_summaries_reports_a_bound_summary_as_present(authed):
    """The other half of the pin: a summary whose row still exists must NOT be flagged, or every
    card on a healthy record carries the badge."""
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))

    body = (await client.get(f"/api/documents/{doc_id}/summaries")).json()

    assert body[0]["rowMissing"] is False


async def test_stranded_summaries_counts_both_losses_separately(authed):
    """The two halves are different losses and are reported apart: an orphan still EXPORTS (the
    pre-edit split reaches the client), an unsummarized row exports nothing."""
    from app.api.documents import stranded_summaries

    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_rows(doc_id, [(1, 2, None), (5, 6, None)])
    _seed_summary(doc_id, idx=0, pages=(1, 2))
    _seed_summary(doc_id, idx=1, pages=(3, 4))

    with get_sessionmaker()() as session:
        orphaned, unsummarized = stranded_summaries(session.get(Document, doc_id))

    assert (orphaned, unsummarized) == (1, 1)


async def test_stranded_summaries_ignores_a_row_the_reviewer_excluded(authed):
    """An EXCLUDED row with no summary is not a loss - the reviewer said not to summarize it, and
    counting it would reopen every record that has one. Only included rows are owed text."""
    from app.api.documents import stranded_summaries

    client, _ = authed
    doc_id = await _upload(client, pages=4)
    _seed_rows(doc_id, [(1, 2, None), (3, 4, None)])
    _seed_summary(doc_id, idx=0, pages=(1, 2))
    with get_sessionmaker()() as session:
        excluded = session.scalar(
            select(ReviewRow).where(ReviewRow.document_id == doc_id, ReviewRow.start == 3)
        )
        excluded.include = False
        session.commit()

    with get_sessionmaker()() as session:
        assert stranded_summaries(session.get(Document, doc_id)) == (0, 0)


async def test_get_summaries_reports_how_the_rows_category_was_decided(authed):
    """WHEN a summary is listed, THE SYSTEM SHALL report which cascade path decided its row's
    CURRENT category, so the Summaries tab can flag a guessed one.

    Adam, 2026-08-31: "an extra tag to show that it wasn't confident would be useful". This is the
    surface that needed it - the defect he reported was an EMG report written up with the evaluation
    checklist, and reading a summary against its source pages is done from here, not from the row
    table.

    The raw method rather than a boolean, so the client keeps ONE definition of "guessed"
    (`review-rows.categoryWasGuessed`) instead of a server copy free to drift from it.
    """
    client, _ = authed
    doc_id = await _upload(client, pages=2)
    _seed_rows(doc_id, [(1, 2, None)])
    _seed_summary(doc_id, pages=(1, 2))
    with get_sessionmaker()() as session:
        row = session.scalar(select(ReviewRow).where(ReviewRow.document_id == doc_id))
        row.method = "llm-disagree"
        session.commit()

    body = (await client.get(f"/api/documents/{doc_id}/summaries")).json()

    assert body[0]["rowMethodLive"] == "llm-disagree"


async def test_a_summary_with_no_live_row_reports_no_method(authed):
    """IF no row covers the summary's pages, THEN the method SHALL be null - there is no live row to
    read one from, and the same absence is already reported by `rowMissing`."""
    client, _ = authed
    doc_id = await _upload(client, pages=3)
    _seed_rows(doc_id, [(1, 1, None)])
    _seed_summary(doc_id, pages=(2, 3))

    body = (await client.get(f"/api/documents/{doc_id}/summaries")).json()

    assert body[0]["rowMissing"] is True
    assert body[0]["rowMethodLive"] is None
    assert body[0]["rowCategoryLive"] is None


def test_an_unopenable_pdf_answers_422_not_503():
    """#201. `PdfUnreadableError` subclasses `OcrUnavailableError` so every internal layer keeps
    refusing to degrade on it - but at the HTTP boundary the two part company. A bad upload is not a
    service outage, and 503 tells the caller to wait for someone to fix the server when what they
    need to do is re-upload the file.

    The isinstance ORDER in `_pipeline_error_response` is what makes this work, so it is pinned."""
    import json

    from app.api.documents import _pipeline_error_response
    from app.errors import EmptyExtractionError, OcrUnavailableError, PdfUnreadableError

    bad_file = _pipeline_error_response("doc-1", PdfUnreadableError("truncated"))
    assert bad_file.status_code == 422
    assert "could not be opened" in json.loads(bad_file.body)["error"]

    # The config failure keeps 503: it IS a server problem and retrying the upload will not help.
    no_binary = _pipeline_error_response("doc-1", OcrUnavailableError("no poppler"))
    assert no_binary.status_code == 503

    # And the sibling document-property error is unchanged.
    assert _pipeline_error_response("doc-1", EmptyExtractionError("blank")).status_code == 422


# #202: `get_status` answered two different questions with one row. "What is happening now" is the
# newest job of any kind; "which sub-documents could not be summarized" belongs to the summarize run
# that recorded it. Reading both off the newest job meant ANY later job hid the row list - measured
# before the fix, 5 of the 6 jobs holding an attention payload were already buried, and the reviewer
# saw "Some documents need attention." with an empty list and no way back.
def _seed_job(doc_id, kind, state, *, attention=None, error=None, stage=None, current=0, total=0):
    with get_sessionmaker()() as session:
        session.add(
            Job(
                document_id=doc_id,
                kind=kind,
                state=state,
                stage=stage,
                current=current,
                total=total,
                error=error,
                attention=attention,
                model="m",
                prompt_version="1",
            )
        )
        session.commit()


# The shape `_finalize_needs_attention` actually stores: `pages` as a string, not start/end. The
# first version of this fixture invented start/end and only the FRONTEND typechecker caught it -
# these tests had passed against the fiction, because the endpoint just passes the JSON through.
_ATTENTION = {
    "rows": [{"idx": 2, "pages": "5-6", "reason": "unreadable"}],
    "message": "2 could not be read",
}


async def test_a_later_dedup_no_longer_hides_the_needs_attention_row_list(authed):
    """The bug. A dedup after a failed summarize displaced the payload entirely."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_job(
        doc_id, "summarize", "needs_attention", attention=_ATTENTION, error="2 could not be read"
    )
    _seed_job(doc_id, "dedup", "done", stage="deduping", current=1, total=1)

    job = (await client.get(f"/api/documents/{doc_id}/status")).json()["job"]

    assert job["kind"] == "dedup"  # progress still describes what is happening NOW
    assert job["state"] == "done"
    assert job["attention"]["rows"][0]["idx"] == 2  # ...and the row list survives
    assert job["attention"]["message"] == "2 could not be read"


async def test_a_successful_re_summarize_clears_the_payload_rather_than_resurrecting_it(authed):
    """The trap in the tempting version. "Newest job that CARRIES attention" would surface the old
    payload forever, because a successful run records none - so the reviewer would keep being told
    about rows they have already fixed. Keying on the summarize KIND gets this right."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_job(
        doc_id, "summarize", "needs_attention", attention=_ATTENTION, error="2 could not be read"
    )
    _seed_job(doc_id, "summarize", "done", stage="summarizing", current=3, total=3)
    _seed_job(doc_id, "dedup", "done", stage="deduping")

    job = (await client.get(f"/api/documents/{doc_id}/status")).json()["job"]

    assert job["kind"] == "dedup"
    assert job["attention"] is None


async def test_the_newest_summarize_wins_when_it_also_needs_attention(authed):
    """A second failed run supersedes the first: its list is the current one."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_job(doc_id, "summarize", "needs_attention", attention=_ATTENTION, error="old")
    later = {
        "rows": [{"idx": 0, "pages": "1-1", "reason": "blank"}],
        "message": "1 could not be read",
    }
    _seed_job(doc_id, "summarize", "needs_attention", attention=later, error="1 could not be read")
    _seed_job(doc_id, "dedup", "done")

    job = (await client.get(f"/api/documents/{doc_id}/status")).json()["job"]

    assert [r["idx"] for r in job["attention"]["rows"]] == [0]
    assert job["attention"]["message"] == "1 could not be read"


async def test_a_running_job_still_reports_its_own_progress(authed):
    """The splice must not disturb the poller: a dedup running after a failed summarize shows its
    own state and stage, which is what drives the progress bar."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_job(doc_id, "summarize", "needs_attention", attention=_ATTENTION)
    _seed_job(doc_id, "dedup", "running", stage="deduping", current=2, total=5)

    job = (await client.get(f"/api/documents/{doc_id}/status")).json()["job"]

    assert (job["kind"], job["state"], job["stage"]) == ("dedup", "running", "deduping")
    assert (job["current"], job["total"]) == (2, 5)
    # The frontend reads `attention` only when state is needs_attention, so carrying it here is
    # inert - but it must be the summarize's, not an empty one.
    assert job["attention"]["rows"][0]["idx"] == 2


async def test_a_document_with_no_summarize_job_reports_no_attention(authed):
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_job(doc_id, "segment", "done", stage="segmenting")
    _seed_job(doc_id, "dedup", "done", stage="deduping")

    job = (await client.get(f"/api/documents/{doc_id}/status")).json()["job"]

    assert job["kind"] == "dedup"
    assert job["attention"] is None


async def test_the_summarize_job_itself_is_unchanged_when_it_is_newest(authed):
    """Guard: the common case must not move. When the needs_attention summarize IS the newest job,
    every field comes from it exactly as before."""
    client, _ = authed
    doc_id = await _upload(client, pages=6)
    _seed_job(
        doc_id, "summarize", "needs_attention", attention=_ATTENTION, error="2 could not be read"
    )

    job = (await client.get(f"/api/documents/{doc_id}/status")).json()["job"]

    assert job["kind"] == "summarize"
    assert job["state"] == "needs_attention"
    assert job["error"] == "2 could not be read"
    assert job["attention"]["rows"][0]["idx"] == 2
