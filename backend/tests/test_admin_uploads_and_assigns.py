"""An admin can upload a record into another reviewer's account, and assign a record to someone.

The client's lead reviewer asked for both on top of opening other reviewers' records: "upload a
document into that user page" and "assign people to work with it", with a confirmation first and a
check that the reviewer does not already have the record. So:

* an upload may name `owner`; an admin's lands in that reviewer's account, anyone else's is refused;
* into someone else's account a file they already hold is a 409 the admin confirms, never a silent
  second copy - while into one's own account it stays the warning it always was;
* `/api/admin/documents/{id}/assign` moves a record to one reviewer, refused while a job runs, and
  asks the same duplicate question;
* every one of these is recorded under the admin, and the record's later jobs run in its NEW
  owner's queue.
"""

import hashlib
import io
from pathlib import Path

import pytest
from pypdf import PdfWriter
from rq import Queue
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AuditLog, Document, Job, User
from app.worker.queues import get_redis, lane_name
from tests.conftest import lanes, unique_test_email

_PASSWORD = "Str0ng#pw1"


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    # Uploads are PHI at rest: keep them in a per-test tmp dir that pytest removes.
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture(autouse=True)
def _empty_queues():
    # The aggregate upload and the job-start test enqueue onto real Redis lanes.
    yield
    lanes("segment").empty()


def _user(name: str, *, is_admin: bool = False, active: bool = True) -> tuple[int, str]:
    email = unique_test_email()
    with get_sessionmaker()() as session:
        user = User(
            email=email,
            name=name,
            password=MrrPasswordHelper().hash(_PASSWORD),
            active=active,
            is_admin=is_admin,
        )
        session.add(user)
        session.commit()
        return user.id, email


def _pdf(pages: int = 1, width: int = 72) -> bytes:
    """A blank PDF. `width` makes two files that differ byte for byte."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _record(owner_id: int, *, sha256: str, pages: int = 2) -> str:
    """A record owned by `owner_id`. No file on disk: the assign route never reads it."""
    with get_sessionmaker()() as session:
        document = Document(
            user_id=owner_id,
            original_filename="synthetic.pdf",
            stored_path="/nonexistent/synthetic.pdf",
            sha256=sha256,
            page_count=pages,
        )
        session.add(document)
        session.commit()
        return document.id


async def _login(client, email: str) -> None:
    await client.post("/api/auth/logout")
    resp = await client.post("/api/auth/login", data={"username": email, "password": _PASSWORD})
    assert resp.status_code == 204


async def _upload(client, data: bytes, **fields):
    return await client.post(
        "/api/documents",
        files={"pdf": ("scan.pdf", data, "application/pdf")},
        data={
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in fields.items()
        },
    )


def _records_of(user_id: int) -> list[Document]:
    with get_sessionmaker()() as session:
        return list(session.scalars(select(Document).where(Document.user_id == user_id)).all())


def _audit(doc_id: str, action: str) -> list[tuple[int, str | None]]:
    with get_sessionmaker()() as session:
        return [
            (row.user_id, row.detail)
            for row in session.scalars(
                select(AuditLog).where(AuditLog.document_id == doc_id, AuditLog.action == action)
            ).all()
        ]


@pytest.fixture
def cast():
    """A reviewer, a second reviewer, and an admin."""
    reviewer_id, reviewer_email = _user("Brian")
    other_id, other_email = _user("Bernie")
    admin_id, admin_email = _user("Adam", is_admin=True)
    return {
        "reviewer_id": reviewer_id,
        "reviewer_email": reviewer_email,
        "other_id": other_id,
        "other_email": other_email,
        "admin_id": admin_id,
        "admin_email": admin_email,
    }


# --- uploading into another reviewer's account -----------------------------------------------


async def test_an_admin_uploads_a_record_into_another_reviewers_account(client, cast, tmp_path):
    """DEMONSTRATES the request: the record lands in the reviewer's list, not the admin's."""
    await _login(client, cast["admin_email"])
    resp = await _upload(client, _pdf(), owner=cast["reviewer_id"])
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]

    with get_sessionmaker()() as session:
        document = session.get(Document, doc_id)
        assert document is not None
        owner_id, stored = document.user_id, Path(document.stored_path)
    assert owner_id == cast["reviewer_id"]
    # Filed under the account it went INTO, like the reviewer's own uploads.
    assert stored.parent == tmp_path / str(cast["reviewer_id"])
    assert stored.exists()
    assert [d.id for d in _records_of(cast["admin_id"])] == []
    # Recorded under the admin who did it, saying whose account it went into.
    assert _audit(doc_id, "upload") == [(cast["admin_id"], f"for user {cast['reviewer_id']}")]

    await _login(client, cast["reviewer_email"])
    listed = [d["id"] for d in (await client.get("/api/documents")).json()]
    assert listed == [doc_id]


async def test_a_reviewer_cannot_upload_into_someone_elses_account(client, cast, tmp_path):
    """Refused, not ignored: ignoring it would file the record somewhere the caller did not ask."""
    await _login(client, cast["reviewer_email"])
    resp = await _upload(client, _pdf(), owner=cast["other_id"])
    assert resp.status_code == 403
    assert _records_of(cast["other_id"]) == []
    assert _records_of(cast["reviewer_id"]) == []
    assert list(tmp_path.rglob("*.pdf")) == []


async def test_a_reviewer_naming_their_own_account_uploads_as_always(client, cast):
    """GUARD: `owner` equal to oneself is an ordinary upload, admin or not."""
    await _login(client, cast["reviewer_email"])
    resp = await _upload(client, _pdf(), owner=cast["reviewer_id"])
    assert resp.status_code == 201, resp.text
    assert _audit(resp.json()["id"], "upload") == [(cast["reviewer_id"], None)]


@pytest.mark.parametrize("which", ["unknown", "switched_off"])
async def test_an_upload_for_an_account_that_cannot_work_is_404(client, cast, tmp_path, which):
    off_id, _ = _user("Gone", active=False)
    owner = 2_000_000_000 if which == "unknown" else off_id
    await _login(client, cast["admin_email"])
    resp = await _upload(client, _pdf(), owner=owner)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "reviewer not found"
    assert list(tmp_path.rglob("*.pdf")) == []


async def test_an_admin_is_asked_before_a_second_copy_lands_in_a_reviewers_account(
    client, cast, tmp_path
):
    """DEMONSTRATES the duplicate check: 409 first, the upload only once confirmed."""
    data = _pdf(pages=3)
    await _login(client, cast["reviewer_email"])
    first = (await _upload(client, data)).json()["id"]

    await _login(client, cast["admin_email"])
    refused = await _upload(client, data, owner=cast["reviewer_id"])
    assert refused.status_code == 409
    body = refused.json()
    assert body["code"] == "duplicate"
    assert body["duplicates"] == [{"id": first, "page_count": 3}]
    # Readable by the admin as it stands: whose, how long - and never the file name.
    assert body["detail"].startswith("Brian already has this record (uploaded ")
    assert body["detail"].endswith(", 3 pages).")
    assert "scan" not in body["detail"]
    # Nothing was kept: no second record, and the refused file is gone from the reviewer's folder.
    assert [d.id for d in _records_of(cast["reviewer_id"])] == [first]
    assert len(list((tmp_path / str(cast["reviewer_id"])).glob("*.pdf"))) == 1

    confirmed = await _upload(client, data, owner=cast["reviewer_id"], allow_duplicate=True)
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["sha256_duplicate"] is True
    assert len(_records_of(cast["reviewer_id"])) == 2


async def test_the_check_is_against_the_reviewers_records_not_the_admins(client, cast):
    """The question is whether the person who will work on it already has it."""
    data = _pdf()
    await _login(client, cast["admin_email"])
    assert (await _upload(client, data)).status_code == 201  # the admin's own copy
    resp = await _upload(client, data, owner=cast["reviewer_id"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["sha256_duplicate"] is False


async def test_a_different_file_is_no_duplicate(client, cast):
    await _login(client, cast["reviewer_email"])
    await _upload(client, _pdf(width=72))
    await _login(client, cast["admin_email"])
    resp = await _upload(client, _pdf(width=144), owner=cast["reviewer_id"])
    assert resp.status_code == 201, resp.text


async def test_into_ones_own_account_a_duplicate_is_still_only_a_warning(client, cast):
    """GUARD: reviewers' own uploads keep the old behaviour - re-running a case is legitimate."""
    data = _pdf()
    await _login(client, cast["reviewer_email"])
    assert (await _upload(client, data)).status_code == 201
    again = await _upload(client, data)
    assert again.status_code == 201
    assert again.json()["sha256_duplicate"] is True


async def test_an_admins_split_upload_lands_in_the_reviewers_account(client, cast):
    await _login(client, cast["admin_email"])
    resp = await client.post(
        "/api/documents/aggregate",
        files=[
            ("pdfs", ("a.pdf", _pdf(), "application/pdf")),
            ("pdfs", ("b.pdf", _pdf(width=144), "application/pdf")),
        ],
        data={"name": "combined", "owner": str(cast["reviewer_id"])},
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    with get_sessionmaker()() as session:
        document = session.get(Document, doc_id)
        assert document is not None
        assert document.user_id == cast["reviewer_id"]
        job = session.scalars(select(Job).where(Job.document_id == doc_id)).one()
        assert job.requested_by == cast["admin_id"]
    assert _audit(doc_id, "aggregate_upload") == [
        (cast["admin_id"], f"for user {cast['reviewer_id']}")
    ]
    # Its classify job waits in the REVIEWER's lane, where their per-reviewer limit applies.
    assert Queue(lane_name("segment", cast["reviewer_id"]), connection=get_redis()).count == 1


async def test_a_reviewer_cannot_split_upload_into_someone_elses_account(client, cast):
    await _login(client, cast["reviewer_email"])
    resp = await client.post(
        "/api/documents/aggregate",
        files=[("pdfs", ("a.pdf", _pdf(), "application/pdf"))],
        data={"owner": str(cast["other_id"])},
    )
    assert resp.status_code == 403
    assert _records_of(cast["other_id"]) == []


# --- assigning a record to someone ----------------------------------------------------------


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


async def _assign(client, doc_id: str, owner: int, *, allow_duplicate: bool = False):
    return await client.post(
        f"/api/admin/documents/{doc_id}/assign",
        json={"owner": owner, "allow_duplicate": allow_duplicate},
    )


async def test_an_admin_assigns_a_record_to_another_reviewer(client, cast):
    """DEMONSTRATES the request: the record moves to the reviewer who will work on it."""
    doc_id = _record(cast["reviewer_id"], sha256=_sha("one"))
    await _login(client, cast["admin_email"])
    resp = await _assign(client, doc_id, cast["other_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": doc_id, "owner": {"id": cast["other_id"], "name": "Bernie"}}
    assert [d.id for d in _records_of(cast["other_id"])] == [doc_id]
    assert _records_of(cast["reviewer_id"]) == []
    assert _audit(doc_id, "assign") == [
        (cast["admin_id"], f"user {cast['reviewer_id']} -> {cast['other_id']}")
    ]


async def test_a_reviewer_cannot_assign_a_record(client, cast):
    doc_id = _record(cast["reviewer_id"], sha256=_sha("one"))
    await _login(client, cast["reviewer_email"])
    assert (await _assign(client, doc_id, cast["other_id"])).status_code == 403
    assert [d.id for d in _records_of(cast["reviewer_id"])] == [doc_id]


async def test_assigning_a_record_that_does_not_exist_is_404(client, cast):
    await _login(client, cast["admin_email"])
    assert (await _assign(client, "no-such-record", cast["other_id"])).status_code == 404


@pytest.mark.parametrize("which", ["unknown", "switched_off"])
async def test_assigning_to_an_account_that_cannot_work_is_404(client, cast, which):
    off_id, _ = _user("Gone", active=False)
    doc_id = _record(cast["reviewer_id"], sha256=_sha("one"))
    await _login(client, cast["admin_email"])
    resp = await _assign(client, doc_id, 2_000_000_000 if which == "unknown" else off_id)
    assert resp.status_code == 404
    assert [d.id for d in _records_of(cast["reviewer_id"])] == [doc_id]


@pytest.mark.parametrize("state", ["queued", "running"])
async def test_a_record_with_a_job_in_flight_is_not_assigned(client, cast, state):
    """Its job is already queued under the old owner; moving the record under it is refused."""
    doc_id = _record(cast["reviewer_id"], sha256=_sha("one"))
    with get_sessionmaker()() as session:
        session.add(
            Job(document_id=doc_id, kind="segment", state=state, model="m", prompt_version="p")
        )
        session.commit()
    await _login(client, cast["admin_email"])
    resp = await _assign(client, doc_id, cast["other_id"])
    assert resp.status_code == 409
    assert "a job is running" in resp.json()["detail"]
    assert [d.id for d in _records_of(cast["reviewer_id"])] == [doc_id]
    assert _audit(doc_id, "assign") == []


async def test_assigning_to_a_reviewer_who_already_holds_the_file_asks_first(client, cast):
    doc_id = _record(cast["reviewer_id"], sha256=_sha("same"), pages=4)
    theirs = _record(cast["other_id"], sha256=_sha("same"), pages=4)
    await _login(client, cast["admin_email"])
    refused = await _assign(client, doc_id, cast["other_id"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "duplicate"
    assert refused.json()["duplicates"] == [{"id": theirs, "page_count": 4}]
    assert refused.json()["detail"].startswith("Bernie already has this record")
    assert [d.id for d in _records_of(cast["reviewer_id"])] == [doc_id]

    confirmed = await _assign(client, doc_id, cast["other_id"], allow_duplicate=True)
    assert confirmed.status_code == 200, confirmed.text
    assert {d.id for d in _records_of(cast["other_id"])} == {doc_id, theirs}


async def test_assigning_a_record_to_its_own_owner_changes_nothing(client, cast):
    """GUARD: no move, so no audit row saying one happened - and the record is not its own
    duplicate."""
    doc_id = _record(cast["reviewer_id"], sha256=_sha("one"))
    await _login(client, cast["admin_email"])
    resp = await _assign(client, doc_id, cast["reviewer_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["id"] == cast["reviewer_id"]
    assert _audit(doc_id, "assign") == []


async def test_after_an_assignment_the_records_jobs_run_in_the_new_reviewers_lane(client, cast):
    doc_id = _record(cast["reviewer_id"], sha256=_sha("one"))
    await _login(client, cast["admin_email"])
    assert (await _assign(client, doc_id, cast["other_id"])).status_code == 200
    resp = await client.post(f"/api/documents/{doc_id}/segment/start", json={})
    assert resp.status_code in (200, 202), resp.text
    assert Queue(lane_name("segment", cast["other_id"]), connection=get_redis()).count == 1
    assert Queue(lane_name("segment", cast["reviewer_id"]), connection=get_redis()).count == 0
