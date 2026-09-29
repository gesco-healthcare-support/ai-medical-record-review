"""An admin can open and fix another reviewer's record; nobody else can.

The client's lead reviewer asked to "see the files that the other people ... are working on, that way
if there is a mistake but they already left, I can go in and fix it myself" - picking one reviewer at
a time rather than seeing everyone's records together. So:

* the ownership guard admits an admin for any record, and everyone else exactly as before (404);
* the records list takes `owner`, honoured for an admin and ignored for anyone else;
* an admin's edits are recorded under the admin, never the owner;
* deleting stays owner-only, even for an admin;
* `/api/admin/users` lists the accounts an admin can pick from.
"""

import pytest
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.db import get_sessionmaker
from app.models import AuditLog, Document, Job, ReviewRow, User
from app.services.seed_catalog import constants_categories
from tests.conftest import unique_test_email

_PASSWORD = "Str0ng#pw1"
_CATEGORY = constants_categories()[0]["id"]


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


def _record(owner_id: int, pages: int = 2) -> str:
    """A record owned by `owner_id`. No file on disk: these routes never read it."""
    with get_sessionmaker()() as session:
        document = Document(
            user_id=owner_id,
            original_filename="synthetic.pdf",
            stored_path="/nonexistent/synthetic.pdf",
            sha256="0" * 64,
            page_count=pages,
        )
        session.add(document)
        session.commit()
        return document.id


async def _login(client, email: str) -> None:
    await client.post("/api/auth/logout")
    resp = await client.post("/api/auth/login", data={"username": email, "password": _PASSWORD})
    assert resp.status_code == 204


@pytest.fixture
def cast():
    """A reviewer who owns a record, a second reviewer, and an admin."""
    owner_id, owner_email = _user("Brian")
    other_id, other_email = _user("Bernie")
    admin_id, admin_email = _user("Adam", is_admin=True)
    return {
        "owner_id": owner_id,
        "owner_email": owner_email,
        "other_id": other_id,
        "other_email": other_email,
        "admin_id": admin_id,
        "admin_email": admin_email,
        "doc_id": _record(owner_id),
    }


# --- everyone who is not an admin: unchanged --------------------------------------------------


async def test_a_reviewer_still_cannot_open_another_reviewers_record(client, cast):
    """GUARD. The admin door must not open for anyone else."""
    await _login(client, cast["other_email"])
    doc = cast["doc_id"]
    assert (await client.get(f"/api/documents/{doc}")).status_code == 404
    rows = [{"start": 1, "end": 2, "category": _CATEGORY}]
    assert (await client.put(f"/api/documents/{doc}/rows", json={"rows": rows})).status_code == 404
    assert (await client.delete(f"/api/documents/{doc}")).status_code == 404


async def test_a_reviewer_asking_for_someone_elses_list_gets_their_own(client, cast):
    """`owner` is ignored, not refused, for a non-admin: the answer reveals nothing either way."""
    mine = _record(cast["other_id"])
    await _login(client, cast["other_email"])
    listed = await client.get("/api/documents", params={"owner": cast["owner_id"]})
    assert listed.status_code == 200
    ids = {d["id"] for d in listed.json()}
    assert mine in ids
    assert cast["doc_id"] not in ids


async def test_a_reviewer_cannot_list_the_accounts(client, cast):
    await _login(client, cast["other_email"])
    assert (await client.get("/api/admin/users")).status_code == 403


# --- an admin -------------------------------------------------------------------------------


async def test_an_admin_opens_another_reviewers_record_and_sees_whose_it_is(client, cast):
    await _login(client, cast["admin_email"])
    resp = await client.get(f"/api/documents/{cast['doc_id']}")
    assert resp.status_code == 200
    assert resp.json()["owner"] == {"id": cast["owner_id"], "name": "Brian"}


async def test_an_owner_sees_themselves_as_the_owner(client, cast):
    await _login(client, cast["owner_email"])
    resp = await client.get(f"/api/documents/{cast['doc_id']}")
    assert resp.json()["owner"]["id"] == cast["owner_id"]


async def test_an_admin_lists_one_reviewers_records_not_everyones(client, cast):
    """The lead reviewer asked to pick a user and see that user's records only."""
    others = _record(cast["other_id"])
    await _login(client, cast["admin_email"])
    listed = await client.get("/api/documents", params={"owner": cast["owner_id"]})
    ids = {d["id"] for d in listed.json()}
    assert cast["doc_id"] in ids
    assert others not in ids


async def test_an_admin_with_no_owner_chosen_sees_only_their_own_records(client, cast):
    """GUARD: the page looks exactly as it did until the admin picks someone."""
    own = _record(cast["admin_id"])
    await _login(client, cast["admin_email"])
    ids = {d["id"] for d in (await client.get("/api/documents")).json()}
    assert own in ids
    assert cast["doc_id"] not in ids


async def test_an_admins_fix_is_saved_and_recorded_under_the_admin(client, cast):
    await _login(client, cast["admin_email"])
    rows = [{"start": 1, "end": 2, "category": _CATEGORY}]
    resp = await client.put(f"/api/documents/{cast['doc_id']}/rows", json={"rows": rows})
    assert resp.status_code == 200, resp.text

    with get_sessionmaker()() as session:
        actors = set(
            session.scalars(
                select(AuditLog.user_id).where(
                    AuditLog.document_id == cast["doc_id"], AuditLog.action == "rows.edit"
                )
            ).all()
        )
        owner_kept = session.get(Document, cast["doc_id"]).user_id
    assert actors == {cast["admin_id"]}
    # The record stays the reviewer's: fixing it does not move it into the admin's list.
    assert owner_kept == cast["owner_id"]


async def test_an_admin_cannot_delete_another_reviewers_record(client, cast):
    """Deleting is not fixing, and cannot be undone. Owner-only even for an admin."""
    await _login(client, cast["admin_email"])
    assert (await client.delete(f"/api/documents/{cast['doc_id']}")).status_code == 404
    with get_sessionmaker()() as session:
        assert session.get(Document, cast["doc_id"]) is not None


async def test_an_admin_can_still_delete_their_own_record(client, cast):
    """GUARD: the owner-only rule must not take deletion away from owners."""
    own = _record(cast["admin_id"])
    await _login(client, cast["admin_email"])
    assert (await client.delete(f"/api/documents/{own}")).status_code == 200


async def test_a_job_an_admin_starts_records_the_admin_as_the_requester(client, cast):
    await _login(client, cast["admin_email"])
    resp = await client.post(f"/api/documents/{cast['doc_id']}/segment/start", json={})
    assert resp.status_code in (200, 202), resp.text
    with get_sessionmaker()() as session:
        job = session.scalars(
            select(Job).where(Job.document_id == cast["doc_id"]).order_by(Job.id.desc())
        ).first()
    assert job is not None
    assert job.requested_by == cast["admin_id"]


async def test_an_admin_lists_the_active_accounts_to_pick_from(client, cast):
    off_id, _ = _user("Switched off", active=False)
    await _login(client, cast["admin_email"])
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 200
    by_id = {u["id"]: u for u in resp.json()}
    assert by_id[cast["owner_id"]] == {
        "id": cast["owner_id"],
        "name": "Brian",
        "email": cast["owner_email"],
    }
    assert off_id not in by_id


# --- every change an admin makes is recorded under the admin ----------------------------------


def _actors(doc_id: str, action: str) -> set[int]:
    with get_sessionmaker()() as session:
        return set(
            session.scalars(
                select(AuditLog.user_id).where(
                    AuditLog.document_id == doc_id, AuditLog.action == action
                )
            ).all()
        )


def _audit_details(doc_id: str, action: str) -> list[str | None]:
    with get_sessionmaker()() as session:
        return list(
            session.scalars(
                select(AuditLog.detail).where(
                    AuditLog.document_id == doc_id, AuditLog.action == action
                )
            ).all()
        )


async def test_an_admins_header_edit_is_recorded_under_the_admin(client, cast):
    await _login(client, cast["admin_email"])
    resp = await client.put(
        f"/api/documents/{cast['doc_id']}/header",
        json={
            "patient_first_name": "Synthetic",
            "patient_last_name": "Patient",
            "patient_dob": "",
            "law_firm": "",
        },
    )
    assert resp.status_code == 200, resp.text
    assert _actors(cast["doc_id"], "header.edit") == {cast["admin_id"]}
    # Field names only: the values are the patient's details and never go in the audit log.
    (detail,) = _audit_details(cast["doc_id"], "header.edit")
    assert detail == "changed=patient_first_name,patient_last_name"
    assert "Synthetic" not in detail


async def test_an_admins_header_detection_is_recorded_under_the_admin(client, cast, monkeypatch):
    import app.api.documents as documents_module

    monkeypatch.setattr(
        documents_module,
        "extract_header",
        lambda pdf_path, pages: {
            "first_name": "Synthetic",
            "last_name": "",
            "dob": "",
            "lawfirm": "",
        },
    )
    await _login(client, cast["admin_email"])
    resp = await client.post(f"/api/documents/{cast['doc_id']}/extract-header")
    assert resp.status_code == 200, resp.text
    assert _actors(cast["doc_id"], "header.extract") == {cast["admin_id"]}
    assert _audit_details(cast["doc_id"], "header.extract") == ["filled=patient_first_name"]


async def test_an_admins_duplicate_resolution_is_recorded_under_the_admin(client, cast):
    with get_sessionmaker()() as session:
        for idx, (start, end) in enumerate(((1, 1), (2, 2))):
            session.add(
                ReviewRow(
                    document_id=cast["doc_id"],
                    idx=idx,
                    start=start,
                    end=end,
                    category=_CATEGORY,
                    dupe_group=1,
                )
            )
        session.commit()
    await _login(client, cast["admin_email"])
    resp = await client.post(
        f"/api/documents/{cast['doc_id']}/duplicates/1/resolve", json={"action": "dismiss"}
    )
    assert resp.status_code == 200, resp.text
    assert _actors(cast["doc_id"], "duplicates.resolve") == {cast["admin_id"]}
    assert _audit_details(cast["doc_id"], "duplicates.resolve") == ["group=1 action=dismiss"]


@pytest.mark.parametrize("kind", ["segment", "dedup"])
async def test_a_job_an_admin_starts_is_audited_under_the_admin(client, cast, kind):
    await _login(client, cast["admin_email"])
    resp = await client.post(f"/api/documents/{cast['doc_id']}/{kind}/start", json={})
    assert resp.status_code in (200, 202), resp.text
    assert _actors(cast["doc_id"], f"{kind}.start") == {cast["admin_id"]}


async def test_an_admin_opening_another_reviewers_record_is_recorded(client, cast):
    await _login(client, cast["admin_email"])
    assert (await client.get(f"/api/documents/{cast['doc_id']}")).status_code == 200
    assert _actors(cast["doc_id"], "view_record") == {cast["admin_id"]}


async def test_an_owner_reading_their_own_record_writes_no_view_row(client, cast):
    """GUARD: only cross-account reads are new, so only they are recorded."""
    await _login(client, cast["owner_email"])
    assert (await client.get(f"/api/documents/{cast['doc_id']}")).status_code == 200
    assert _actors(cast["doc_id"], "view_record") == set()
