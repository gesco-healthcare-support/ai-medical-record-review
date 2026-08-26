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
from app.services.rows import validate_rows
from tests.conftest import unique_test_email

_TEST_CAT_PREFIX = "900"  # test category ids: 9001, 9002, ...


@pytest.fixture(autouse=True)
def _clean_test_categories():
    """Restore the catalog to exactly the rows that existed before the test.

    Deleting the 900x ids is no longer enough. Creating a category now materializes the taxonomy
    constants first (otherwise that one insert collapses the catalog - see
    test_creating_a_category_leaves_every_other_category_valid), so a create leaves ~16 built-in rows
    behind as well. Left in place they would flip this shared database from unseeded to seeded for
    every later test AND make the collapse test non-demonstrating on its second run, so the snapshot
    is taken after the pre-clean and anything new is removed.
    """

    def category_ids(session):
        return set(session.scalars(select(Category.id)).all())

    def drop(session, ids):
        if ids:
            session.execute(delete(Prompt).where(Prompt.category_id.in_(ids)))
            session.execute(delete(Category).where(Category.id.in_(ids)))
            session.commit()

    with get_sessionmaker()() as session:
        drop(session, [i for i in category_ids(session) if i.startswith(_TEST_CAT_PREFIX)])
        before = category_ids(session)

    yield

    with get_sessionmaker()() as session:
        drop(session, list(category_ids(session) - before))


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
    assert body["summarize_default"] is True  # on by default

    # It shows up in the listing.
    listing = (await admin_client.get("/api/admin/categories")).json()
    assert any(c["id"] == "9001" for c in listing)

    # summarize_default can be set off at creation.
    off = await admin_client.post(
        "/api/admin/categories",
        json={"id": "9005", "name": "Off", "summarize_default": False},
    )
    assert off.status_code == 201 and off.json()["summarize_default"] is False

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


async def test_creating_a_category_leaves_every_other_category_valid(admin_client):
    """DEMONSTRATES the bug end to end: on origin/main this leaves the catalog holding 9006 alone.

    `catalog.get_categories` falls back to the taxonomy constants only while `categories` is EMPTY,
    which is the normal state for a fresh box, local dev and CI - nothing in app/ seeds it. So the
    first category an admin created ended that fallback and took every other category with it. What
    the reviewer saw was every document failing to autosave with 400 "unknown category".

    Whether this test demonstrates or merely guards depends on the database starting unseeded, which
    `_clean_test_categories` restores after each test precisely so it keeps demonstrating.
    """
    from app.services import catalog

    created = await admin_client.post(
        "/api/admin/categories", json={"id": "9006", "name": "Brand New"}
    )
    assert created.status_code == 201

    with get_sessionmaker()() as session:
        ids = catalog.get_category_ids(session, active_only=True)
        assert "9006" in ids
        for category_id in ("1", "3", "5", "10", "13", "15", "100"):
            assert category_id in ids, f"category {category_id} was destroyed by creating 9006"
        # The half a reviewer feels: rows carrying a built-in category still save.
        assert validate_rows(session, [{"start": 1, "end": 2, "category": "1"}], 5) is None
        # ...and the reason the fix seeds categories only: prompts still resolve through prompts.py,
        # so a deployed prompt change still reaches this box (migration f1a83b5c60d2).
        assert session.scalar(select(Prompt).where(Prompt.role == "summary")) is None

    # An id the constants already carry is now a conflict, not a shadow row beside the built-in.
    dup = await admin_client.post("/api/admin/categories", json={"id": "13", "name": "Shadow"})
    assert dup.status_code == 400 and "already exists" in dup.json()["detail"]


async def test_update_category_soft_delete(admin_client):
    await admin_client.post("/api/admin/categories", json={"id": "9003", "name": "Soft"})
    resp = await admin_client.patch("/api/admin/categories/9003", json={"active": False})
    assert resp.status_code == 200 and resp.json()["active"] is False
    # Soft-deleted, not gone: still present in the admin listing.
    listing = (await admin_client.get("/api/admin/categories")).json()
    assert any(c["id"] == "9003" and c["active"] is False for c in listing)
    # summarize_default toggles via PATCH.
    toggled = await admin_client.patch(
        "/api/admin/categories/9003", json={"summarize_default": False}
    )
    assert toggled.status_code == 200 and toggled.json()["summarize_default"] is False
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


async def test_prompt_delete_reverts_to_the_built_in(admin_client):
    """WHEN a category has a custom row, reverting SHALL delete it so the code prompt applies again;
    the response reports the built-in text now in effect."""
    await admin_client.post("/api/admin/categories", json={"id": "9005", "name": "Revertible"})
    await admin_client.put("/api/admin/prompts/9005", json={"text": "Custom text."})
    assert (await admin_client.get("/api/admin/prompts/9005")).json()["custom"] is True

    deleted = await admin_client.delete("/api/admin/prompts/9005")
    assert deleted.status_code == 200
    assert deleted.json()["custom"] is False

    got = (await admin_client.get("/api/admin/prompts/9005")).json()
    assert got["text"] is None and got["custom"] is False
    # 9005 has no code prompt of its own, so the effective text is the general one.
    assert got["effective_text"]

    # Nothing to revert -> 404, so the UI cannot offer it on a built-in prompt.
    assert (await admin_client.delete("/api/admin/prompts/9005")).status_code == 404
    assert (await admin_client.delete("/api/admin/prompts/9998")).status_code == 404


async def test_prompt_delete_requires_superuser(client, seeded_user):
    email, password = seeded_user
    await client.post("/api/auth/login", data={"username": email, "password": password})
    assert (await client.delete("/api/admin/prompts/1")).status_code == 403


async def test_reprocess_acts_on_any_owner(admin_client):
    from tests.conftest import lanes

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

    queue = lanes("summarize")
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
