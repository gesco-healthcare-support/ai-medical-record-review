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
    test_creating_a_category_leaves_every_other_category_valid), so a create leaves ~16 built-in
    rows behind as well. Left in place they would flip this shared database from unseeded to seeded
    for every later test AND make the collapse test non-demonstrating on its second run, so the
    snapshot is taken after the pre-clean and anything new is removed.
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


async def test_the_admin_page_shows_the_built_ins_without_writing_them(admin_client):
    """DEMONSTRATES the bug: on origin/main this returns [] on an unseeded catalog.

    The catalog serves sixteen categories from the constants while `categories` is empty - the
    normal state for a fresh box, local dev and CI - but this route read the raw table, so the admin
    page claimed the app had no categories at all. That emptiness is also what steered an admin into
    creating one, which used to collapse the catalog.
    """
    listing = (await admin_client.get("/api/admin/categories")).json()
    ids = {c["id"] for c in listing}
    for category_id in ("1", "3", "5", "6", "10", "13", "15", "100"):
        assert category_id in ids
    # Same shape as a row-backed category, or the admin UI cannot render it.
    general = next(c for c in listing if c["id"] == "100")
    assert general["summarize_default"] is False and general["active"] is True
    assert "has_summary_prompt" in general and "examples" in general

    # A GET must not write - it is cached, prefetched and repeated. The edit routes seed instead.
    with get_sessionmaker()() as session:
        assert session.scalars(select(Category.id)).all() == []


async def test_a_built_in_category_can_be_edited_on_a_fresh_box(admin_client):
    """DEMONSTRATES the bug: on origin/main both of these are 404 until somebody creates a category.

    The catalog serves the built-ins from the constants, but there is no ROW to edit, so the whole
    catalog was read-only on a fresh box - and once the create path was guarded, creating a category
    was the only way out of that state.
    """
    patched = await admin_client.patch(
        "/api/admin/categories/13", json={"summarize_default": False}
    )
    assert patched.status_code == 200
    assert patched.json()["summarize_default"] is False
    assert patched.json()["id"] == "13"

    # Seeding materialized the whole catalog, not just the row being edited.
    with get_sessionmaker()() as session:
        assert len(session.scalars(select(Category.id)).all()) > 1
        assert validate_rows(session, [{"start": 1, "end": 2, "category": "1"}], 5) is None


async def test_a_built_in_category_can_take_a_custom_prompt_on_a_fresh_box(admin_client):
    put = await admin_client.put("/api/admin/prompts/13", json={"text": "Summarize this."})
    assert put.status_code == 200 and put.json()["custom"] is True
    # An id that is not a category at all is still a 404 - seeding must not invent one.
    assert (
        await admin_client.put("/api/admin/prompts/9998", json={"text": "x"})
    ).status_code == 404


def _row_in_category(category: str) -> str:
    """A document with one review row carrying `category`; returns the document id."""
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
                category=category,
                title="A",
                date="-",
                injury_date="-",
                flag="-",
                include=True,
            )
        )
        session.commit()
        return document.id


async def test_a_category_in_use_cannot_be_deactivated(admin_client):
    """DEMONSTRATES the bug: on origin/main this 200s and every document holding the category
    becomes unsaveable.

    `validate_rows` accepts only ACTIVE categories and `_store_rows` runs it on every save, so
    deactivating a category in use makes the app reject rows the app itself wrote - autosave and
    Summarize both 400 with "unknown category", for every owner, with nothing naming the cause. The
    only way out is hand-editing every affected row, or re-activating.

    Already known here: catalog.get_prompt's docstring records that category 11 "is not deactivated
    either" for exactly this reason, and migration b3f7c02e91a4 refuses to delete category 15 while
    any review row references it. Only this endpoint had no such check.
    """
    from app.services import catalog
    from app.services.rows import validate_rows

    # A 900x category rather than a built-in, so this does not depend on the catalog having been
    # materialized: PATCH needs a ROW to edit, and on an unseeded box the built-ins have none.
    await admin_client.post("/api/admin/categories", json={"id": "9009", "name": "In use"})
    _row_in_category("9009")

    resp = await admin_client.patch("/api/admin/categories/9009", json={"active": False})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "9009" in detail and "cannot be deactivated" in detail

    # The half a reviewer feels: a row carrying that category still saves.
    with get_sessionmaker()() as session:
        assert validate_rows(session, [{"start": 1, "end": 2, "category": "9009"}], 5) is None
        assert "9009" in catalog.get_category_ids(session, active_only=True)


async def test_an_unused_category_can_still_be_deactivated(admin_client):
    """GUARDS against over-correcting: the soft-delete has to keep working, or a mistyped category
    could never be retired."""
    await admin_client.post("/api/admin/categories", json={"id": "9007", "name": "Unused"})
    resp = await admin_client.patch("/api/admin/categories/9007", json={"active": False})
    assert resp.status_code == 200 and resp.json()["active"] is False


async def test_reactivating_a_category_in_use_is_not_blocked(admin_client):
    """The guard is on the DEACTIVATE transition only. Re-activating is the recovery path for a
    category deactivated before this guard existed, so it must never be refused."""
    await admin_client.post("/api/admin/categories", json={"id": "9008", "name": "Off"})
    await admin_client.patch("/api/admin/categories/9008", json={"active": False})
    _row_in_category("9008")

    resp = await admin_client.patch("/api/admin/categories/9008", json={"active": True})
    assert resp.status_code == 200 and resp.json()["active"] is True
