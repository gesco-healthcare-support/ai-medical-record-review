"""The team queue: records put together from received files belong to nobody until a reviewer starts
working on them (the office's answer, 2026-11-25).

- POST /api/documents/combine puts the record in the queue (`Document.team_queue_since`), unless an
  admin uploads it into a named reviewer's account;
- GET /api/documents/queue lists the queue for every reviewer; GET /api/documents (one's own
  records) leaves it out;
- every reviewer may open a record in the queue (`get_owned_document`); only the uploader may
  delete it;
- starting Identify takes it (`_take_from_team_queue`): it becomes the caller's, leaves the queue,
  and its job runs in the caller's lane; of two pressing at once the second is told who has it;
- an admin's Assign takes it out of the queue for the reviewer named.

Synthetic data only: blank PDF pages, made-up names. No model, no OCR.
"""

import pytest
from fastapi import HTTPException
from rq import Queue
from sqlalchemy import select

from app.api.documents import _take_from_team_queue
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AuditLog, Document, User
from app.services import received_files
from app.worker.queues import OCR_QUEUE, get_redis, queue_for
from tests.conftest import lanes
from tests.test_received_files import _combine, _document, _login, _pdf, _user


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_reading(monkeypatch):
    """No OCR and no model in a test: a staged file's first page reads as nothing."""
    monkeypatch.setattr(received_files, "extract_text_from_selected_pages", lambda *a, **k: "")

    def off(stage):
        raise ConnectionError("no model in tests")

    monkeypatch.setattr(received_files, "provider_for_stage", off)


@pytest.fixture(autouse=True)
def _empty_queues():
    ocr = Queue(OCR_QUEUE, connection=get_redis())
    ocr.empty()
    lanes("segment").empty()
    yield
    ocr.empty()
    lanes("segment").empty()


async def _queued(client) -> tuple[str, int, str]:
    """A record put in the team queue by a fresh uploader; logged in as that uploader."""
    uploader, email = _user()
    await _login(client, email)
    resp = await _combine(client)
    assert resp.status_code == 201, resp.text
    assert resp.json()["in_team_queue"] is True
    return resp.json()["id"], uploader, email


def _audit(doc_id: str, action: str) -> list[tuple[int, str | None]]:
    with get_sessionmaker()() as session:
        rows = session.execute(
            select(AuditLog.user_id, AuditLog.detail).where(
                AuditLog.document_id == doc_id, AuditLog.action == action
            )
        ).all()
        return [(row.user_id, row.detail) for row in rows]


# --- into the queue -------------------------------------------------------------------------------


async def test_a_record_put_together_from_received_files_goes_into_the_team_queue(client):
    """DEMONSTRATES: it waits in the queue, not in its uploader's own records."""
    doc_id, uploader, _ = await _queued(client)

    document = _document(doc_id)
    own = (await client.get("/api/documents")).json()
    queue = (await client.get("/api/documents/queue")).json()

    assert document.team_queue_since is not None
    assert document.user_id == uploader  # the uploader's id stands in while it waits
    assert [doc["id"] for doc in own] == []
    assert [(doc["id"], doc["in_team_queue"], doc["uploaded_by_me"]) for doc in queue] == [
        (doc_id, True, True)
    ]
    assert queue[0]["uploaded_by"] == "Synthetic"
    assert _audit(doc_id, "combine_upload") == [(uploader, "3 files, to the team queue")]


async def test_every_reviewer_sees_the_team_queue(client):
    doc_id, _, _ = await _queued(client)
    _, other = _user()
    await _login(client, other)

    queue = (await client.get("/api/documents/queue")).json()

    assert [(doc["id"], doc["uploaded_by_me"]) for doc in queue] == [(doc_id, False)]


async def test_an_admin_uploading_for_a_named_reviewer_skips_the_queue(client):
    """GUARD: the admin chose who works on it."""
    reviewer, _ = _user()
    _, admin = _user(is_admin=True)
    await _login(client, admin)

    resp = await _combine(client, owner=str(reviewer))

    assert resp.status_code == 201, resp.text
    assert resp.json()["in_team_queue"] is False
    assert _document(resp.json()["id"]).team_queue_since is None
    assert (await client.get("/api/documents/queue")).json() == []


async def test_a_single_pdf_upload_stays_with_its_uploader(client):
    """GUARD: only a record put together from received files goes into the queue."""
    _, email = _user()
    await _login(client, email)

    resp = await client.post(
        "/api/documents", files={"pdf": ("record.pdf", _pdf(), "application/pdf")}
    )

    assert resp.status_code == 201, resp.text
    assert [doc["id"] for doc in (await client.get("/api/documents")).json()] == [resp.json()["id"]]
    assert (await client.get("/api/documents/queue")).json() == []


# --- open, but not delete -------------------------------------------------------------------------


async def test_any_reviewer_may_open_a_record_in_the_queue_and_its_billing_sheet(client):
    doc_id, _, _ = await _queued(client)
    _, other = _user()
    await _login(client, other)

    opened = await client.get(f"/api/documents/{doc_id}")
    sheet = await client.get(f"/api/documents/{doc_id}/billing")

    assert opened.status_code == 200, opened.text
    assert sheet.status_code == 200, sheet.text
    # Opening takes nothing.
    assert _document(doc_id).team_queue_since is not None


async def test_a_record_another_reviewer_holds_stays_closed(client):
    """GUARD: the queue opens records nobody has taken, nothing else."""
    _, owner_email = _user()
    await _login(client, owner_email)
    resp = await client.post(
        "/api/documents", files={"pdf": ("record.pdf", _pdf(), "application/pdf")}
    )
    _, other = _user()
    await _login(client, other)

    assert (await client.get(f"/api/documents/{resp.json()['id']}")).status_code == 404


async def test_only_the_uploader_may_delete_a_record_in_the_queue(client):
    doc_id, _, uploader_email = await _queued(client)
    _, other = _user()
    await _login(client, other)

    refused = await client.delete(f"/api/documents/{doc_id}")
    await _login(client, uploader_email)
    deleted = await client.delete(f"/api/documents/{doc_id}")

    assert refused.status_code == 404
    assert deleted.status_code == 200, deleted.text


# --- taken by starting Identify -------------------------------------------------------------------


async def test_starting_identify_takes_the_record_into_the_reviewers_own_records(client):
    """DEMONSTRATES: the record becomes the caller's, leaves the queue, and its Identify runs in the
    caller's lane."""
    doc_id, uploader, _ = await _queued(client)
    taker, email = _user()
    await _login(client, email)

    resp = await client.post(f"/api/documents/{doc_id}/segment/start")

    assert resp.status_code == 200, resp.text
    document = _document(doc_id)
    assert (document.user_id, document.team_queue_since) == (taker, None)
    assert [doc["id"] for doc in (await client.get("/api/documents")).json()] == [doc_id]
    assert (await client.get("/api/documents/queue")).json() == []
    assert queue_for("segment", taker).count == 1
    assert queue_for("segment", uploader).count == 0
    assert _audit(doc_id, "team_queue.take") == [(taker, f"uploaded by user {uploader}")]


async def test_the_uploader_may_take_their_own_upload(client):
    doc_id, uploader, _ = await _queued(client)

    resp = await client.post(f"/api/documents/{doc_id}/segment/start")

    assert resp.status_code == 200, resp.text
    assert (_document(doc_id).user_id, _document(doc_id).team_queue_since) == (uploader, None)
    assert [doc["id"] for doc in (await client.get("/api/documents")).json()] == [doc_id]


async def test_once_taken_the_record_is_closed_to_the_others(client):
    doc_id, _, _ = await _queued(client)
    _, first = _user()
    _, second = _user()
    await _login(client, first)
    await client.post(f"/api/documents/{doc_id}/segment/start")
    await _login(client, second)

    assert (await client.post(f"/api/documents/{doc_id}/segment/start")).status_code == 404
    assert (await client.get(f"/api/documents/{doc_id}")).status_code == 404


async def test_of_two_pressing_at_once_the_second_is_told_who_has_it(client):
    """DEMONSTRATES: both passed the guard while the record waited; the first takes it, and the
    second - holding the record as it was read - takes nothing and is told who has it."""
    doc_id, _, _ = await _queued(client)
    first_id, first = _user()
    second_id, _ = _user()
    with get_sessionmaker()() as session:
        session.get(User, first_id).name = "Reviewer A"
        session.commit()

    with get_sessionmaker()() as late:
        stale = late.get(Document, doc_id)  # read while it still waits
        assert stale is not None and stale.team_queue_since is not None
        await _login(client, first)
        assert (await client.post(f"/api/documents/{doc_id}/segment/start")).status_code == 200

        with pytest.raises(HTTPException) as refused:
            _take_from_team_queue(late, stale, late.get(User, second_id))

    assert refused.value.status_code == 409
    assert refused.value.detail == (
        "Reviewer A has just taken this record from the team queue. Refresh the page."
    )
    assert _document(doc_id).user_id == first_id


def test_a_record_outside_the_queue_is_left_as_it_is():
    """GUARD: taking applies only to a record that waits."""
    with get_sessionmaker()() as session:
        owner_id, _ = _user()
        other_id, _ = _user()
        document = Document(
            user_id=owner_id,
            original_filename="x.pdf",
            stored_path="x.pdf",
            sha256="0" * 64,
            page_count=1,
        )
        session.add(document)
        session.commit()

        _take_from_team_queue(session, document, session.get(User, other_id))

        assert document.user_id == owner_id


# --- assigned -------------------------------------------------------------------------------------


async def test_assigning_a_record_takes_it_out_of_the_queue(client):
    doc_id, _, _ = await _queued(client)
    reviewer, _ = _user()
    _, admin = _user(is_admin=True)
    await _login(client, admin)

    resp = await client.post(f"/api/admin/documents/{doc_id}/assign", json={"owner": reviewer})

    assert resp.status_code == 200, resp.text
    assert (_document(doc_id).user_id, _document(doc_id).team_queue_since) == (reviewer, None)
    assert _audit(doc_id, "assign")[0][1] == f"team queue -> {reviewer}"


async def test_assigning_a_record_to_its_uploader_takes_it_out_of_the_queue_too(client):
    """The uploader's id only stands in while it waits; assigning it to them is a real move."""
    doc_id, uploader, _ = await _queued(client)
    _, admin = _user(is_admin=True)
    await _login(client, admin)

    resp = await client.post(f"/api/admin/documents/{doc_id}/assign", json={"owner": uploader})

    assert resp.status_code == 200, resp.text
    assert (_document(doc_id).user_id, _document(doc_id).team_queue_since) == (uploader, None)
    assert _audit(doc_id, "assign")[0][1] == f"team queue -> {uploader}"
