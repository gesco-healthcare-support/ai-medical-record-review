"""P5 integration tests for the /api/admin router (docker Postgres).

Admin routes are protected by the app-level gate AND a per-route superuser dep, so a non-admin
gets 403 and an anonymous request 401. Category/prompt edits bump the catalog revision. reprocess
is admin-scoped (acts on any owner's document) and reuses the summarize enqueue (real Redis; the
queue is emptied after). Test categories use a 900x id range and are cleaned each test (categories
are not user-scoped, so conftest's user cleanup does not reach them).
"""

import uuid

import pytest
from sqlalchemy import delete, select

from app.auth.password import MrrPasswordHelper
from app.db import get_sessionmaker
from app.models import Category, Document, Prompt, ReviewRow, User
from tests.conftest import unique_test_email

_TEST_CAT_PREFIX = "900"  # test category ids: 9001, 9002, ...


@pytest.fixture(autouse=True)
def _clean_test_categories():
    def clean():
        with get_sessionmaker()() as session:
            ids = session.scalars(
                select(Category.id).where(Category.id.like(_TEST_CAT_PREFIX + "%"))
            ).all()
            if ids:
                session.execute(delete(Prompt).where(Prompt.category_id.in_(ids)))
                session.execute(delete(Category).where(Category.id.in_(ids)))
                session.commit()

    clean()
    yield
    clean()


async def _login(client, *, is_admin: bool):
    email, password = unique_test_email(), "Str0ng#pw1"
    with get_sessionmaker()() as session:
        session.add(
            User(
                email=email,
                name="Admin" if is_admin else "User",
                password=MrrPasswordHelper().hash(password),
                active=True,
                is_admin=is_admin,
            )
        )
        session.commit()
    resp = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 204
    return client


@pytest.fixture
async def admin_client(client):
    return await _login(client, is_admin=True)


async def test_anonymous_and_non_admin_are_blocked(client):
    # No session -> the app-level gate denies (401 for a JSON client).
    assert (await client.get("/api/admin/categories")).status_code == 401
    # Authenticated but not an admin -> 403 (app gate + the router's superuser dep).
    await _login(client, is_admin=False)
    assert (await client.get("/api/admin/categories")).status_code == 403
    assert (await client.get("/api/admin/whoami")).status_code == 403


async def test_whoami(admin_client):
    body = (await admin_client.get("/api/admin/whoami")).json()
    assert body["is_admin"] is True and "@" in body["email"]


async def test_list_categories_returns_a_list(admin_client):
    resp = await admin_client.get("/api/admin/categories")
    assert resp.status_code == 200 and isinstance(resp.json(), list)


async def test_create_category_and_validation(admin_client):
    created = await admin_client.post(
        "/api/admin/categories",
        json={"id": "9001", "name": "Test Category", "examples": ["a", "b"]},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == "9001" and body["name"] == "Test Category"
    assert body["has_summary_prompt"] is False

    # It shows up in the listing.
    listing = (await admin_client.get("/api/admin/categories")).json()
    assert any(c["id"] == "9001" for c in listing)

    # Validation: non-numeric id, duplicate, empty name -> 400.
    assert (
        await admin_client.post("/api/admin/categories", json={"id": "9x", "name": "X"})
    ).status_code == 400
    assert (
        await admin_client.post("/api/admin/categories", json={"id": "9001", "name": "Dup"})
    ).status_code == 400
    assert (
        await admin_client.post("/api/admin/categories", json={"id": "9002", "name": "   "})
    ).status_code == 400


async def test_update_category_soft_delete(admin_client):
    await admin_client.post("/api/admin/categories", json={"id": "9003", "name": "Soft"})
    resp = await admin_client.patch("/api/admin/categories/9003", json={"active": False})
    assert resp.status_code == 200 and resp.json()["active"] is False
    # Soft-deleted, not gone: still present in the admin listing.
    listing = (await admin_client.get("/api/admin/categories")).json()
    assert any(c["id"] == "9003" and c["active"] is False for c in listing)
    # Unknown category -> 404.
    assert (
        await admin_client.patch("/api/admin/categories/9999", json={"name": "z"})
    ).status_code == 404


async def test_prompt_get_and_put(admin_client):
    await admin_client.post("/api/admin/categories", json={"id": "9004", "name": "Prompted"})
    put = await admin_client.put("/api/admin/prompts/9004", json={"text": "Summarize this."})
    assert put.status_code == 200 and put.json()["custom"] is True

    got = (await admin_client.get("/api/admin/prompts/9004")).json()
    assert got["text"] == "Summarize this." and got["custom"] is True

    # Empty prompt text -> 400; unknown category -> 404.
    assert (
        await admin_client.put("/api/admin/prompts/9004", json={"text": "  "})
    ).status_code == 400
    assert (
        await admin_client.put("/api/admin/prompts/9998", json={"text": "x"})
    ).status_code == 404


async def test_reprocess_acts_on_any_owner(admin_client):
    from app.worker.queues import queue_for

    # A document owned by a DIFFERENT (non-admin) user, with an included row.
    with get_sessionmaker()() as session:
        owner = User(
            email=unique_test_email(),
            name="Owner",
            password=MrrPasswordHelper().hash("Str0ng#pw1"),
            active=True,
        )
        session.add(owner)
        session.flush()
        document = Document(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            original_filename="synthetic.pdf",
            stored_path="/nonexistent/synthetic.pdf",
            sha256="0" * 64,
            page_count=1,
        )
        session.add(document)
        session.flush()
        session.add(
            ReviewRow(
                document_id=document.id,
                idx=0,
                start=1,
                end=1,
                category="1",
                title="A",
                date="-",
                injury_date="-",
                flag="-",
                include=True,
            )
        )
        session.commit()
        doc_id = document.id

    queue = queue_for("summarize")
    queue.empty()
    try:
        resp = await admin_client.post(f"/api/admin/reprocess/{doc_id}")
        assert resp.status_code == 200  # admin reprocesses another user's document
        assert queue.count == 1
    finally:
        queue.empty()


async def test_reprocess_unknown_and_no_rows(admin_client):
    assert (await admin_client.post("/api/admin/reprocess/does-not-exist")).status_code == 404

    # A document with no included rows -> 400.
    with get_sessionmaker()() as session:
        owner = User(
            email=unique_test_email(),
            name="Owner",
            password=MrrPasswordHelper().hash("Str0ng#pw1"),
            active=True,
        )
        session.add(owner)
        session.flush()
        document = Document(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            original_filename="synthetic.pdf",
            stored_path="/nonexistent/synthetic.pdf",
            sha256="1" * 64,
            page_count=1,
        )
        session.add(document)
        session.commit()
        doc_id = document.id

    assert (await admin_client.post(f"/api/admin/reprocess/{doc_id}")).status_code == 400
