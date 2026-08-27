"""Admin JSON API: category + prompt administration + admin reprocess (ported from admin_api).

All routes are under /api/admin, protected by the app-level auth gate (P2b: 403 for non-superusers)
AND a per-route superuser dependency here (defense in depth). Every successful edit bumps the
catalog revision - which invalidates the worker classifier caches (they poll catalog_version()) and
stamps subsequent jobs - and writes an audit row. Category ids are immutable; deactivation
soft-deletes. `reprocess` is admin-scoped: it acts on ANY owner's document (no owner filter).
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import current_superuser
from app.config import get_settings
from app.db import get_db
from app.models import Category, Document, Prompt, ReviewRow, User
from app.schemas.admin import CategoryCreate, CategoryUpdate, PromptPut
from app.services import catalog
from app.services.audit import audit
from app.services.gemini import PROMPT_VERSION
from app.services.jobs import JobConflict, enqueue
from app.services.seed_catalog import seed_categories

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_superuser)])

_ID_RE = re.compile(r"^\d+$")


def _has_summary_prompt(session: Session, category_id: str) -> bool:
    return (
        session.scalar(
            select(Prompt).where(Prompt.role == "summary", Prompt.category_id == category_id)
        )
        is not None
    )


def _category_payload(session: Session, category: Category) -> dict:
    data = category.listing()
    data["has_summary_prompt"] = _has_summary_prompt(session, category.id)
    return data


def _builtin_payload(session: Session, listing: dict) -> dict:
    """The same shape for a category that exists only as a constant, so the admin page can show it.

    `listing()` and `constants_categories()` already agree field for field - that is stated where the
    constants are defined - so nothing needs mapping here.
    """
    return {**listing, "has_summary_prompt": _has_summary_prompt(session, listing["id"])}


@router.get("/whoami")
def whoami(user: User = Depends(current_superuser)):
    return {"email": user.email, "is_admin": bool(user.is_superuser)}


@router.get("/categories")
def list_categories(session: Session = Depends(get_db)):
    """Every category the app actually uses, whether or not it has a row yet.

    This reads the CATALOG, not the raw table, and the difference only shows on a catalog that has
    never been written to - which is the normal state for a fresh box, local dev and CI, because
    nothing in `app/` seeds. Reading the table there returned an empty list, so the admin page said
    the app had no categories at all while every reviewer was happily using sixteen of them.

    That emptiness was also what steered an admin into creating one, which used to collapse the
    catalog. Showing the built-ins removes the reason to click Add at all.

    Deliberately does NOT seed. A GET must not write - it is cached, prefetched and repeated - so
    the two edit routes materialize on demand instead, and this one stays a pure read.
    """
    rows = {row.id: row for row in session.scalars(select(Category)).all()}
    return [
        _category_payload(session, rows[c["id"]])
        if c["id"] in rows
        else _builtin_payload(session, c)
        for c in catalog.get_categories(session)
    ]


@router.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    session: Session = Depends(get_db),
    user: User = Depends(current_superuser),
):
    category_id = payload.id.strip()
    name = payload.name.strip()
    if not _ID_RE.match(category_id):
        raise HTTPException(status_code=400, detail="category id must be a positive number")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    # Write the constants out FIRST, or this insert is a deletion. `catalog.get_categories` falls
    # back to taxonomy.py only while `categories` is EMPTY, and empty is the normal state for a
    # fresh box, local dev and CI (nothing in app/ seeds). One unguarded row ends that fallback for
    # every reader at once - get_category_ids, classification._allowed_ids, rows.validate_rows - so
    # the catalog collapses to the row just created and every reviewer gets 400 "unknown category"
    # on autosave. Migration b3f7c02e91a4 guards precisely this; this endpoint did not.
    #
    # Ordering: before the duplicate check, so creating an id the constants already carry reports
    # "already exists" instead of inserting a shadow row beside the built-in one.
    seed_categories(session)
    if session.get(Category, category_id) is not None:
        raise HTTPException(status_code=400, detail=f"category {category_id} already exists")

    category = Category(
        id=category_id,
        name=name,
        description=(payload.description or "").strip(),
        examples=payload.examples,
        active=payload.active,
        auto_assign=payload.auto_assign,
        summarize_default=payload.summarize_default,
    )
    session.add(category)
    session.commit()
    catalog.bump_revision(session)
    audit(session, "category.create", user.id)
    return _category_payload(session, category)


@router.patch("/categories/{category_id}")
def update_category(
    category_id: str,
    payload: CategoryUpdate,
    session: Session = Depends(get_db),
    user: User = Depends(current_superuser),
):
    # Materialize first, or every built-in is a 404 here: the catalog serves them from the
    # constants, but there is no ROW to edit until something writes one. On a fresh box that made
    # the whole catalog read-only, and after the create path was guarded it left creating a category
    # as the only way out of that state. A PATCH is a write, so seeding inside it costs nothing.
    seed_categories(session)
    category = session.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="not found")
    body = payload.model_dump(exclude_unset=True)  # id is immutable and not in the schema
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        category.name = name
    if "description" in body:
        category.description = (body["description"] or "").strip()
    if "examples" in body:
        category.examples = body["examples"] or []
    if "auto_assign" in body:
        category.auto_assign = bool(body["auto_assign"])
    if "summarize_default" in body:
        category.summarize_default = bool(body["summarize_default"])
    if "active" in body:
        active = bool(body["active"])
        # Deactivating a category IN USE makes every document holding it unsaveable, for every
        # owner. `rows.validate_rows` accepts only ids in get_category_ids(active_only=True) and
        # `_store_rows` runs it on every save, so removing an id from that set makes the app reject
        # rows the app itself wrote: autosave and Summarize both 400 with "unknown category", and
        # nothing in the API or the UI names the deactivated category as the cause. The only way out
        # is hand-editing every affected row, or re-activating.
        #
        # The codebase already knew: catalog.get_prompt's docstring records that category 11 "is not
        # deactivated either" for exactly this reason, and migration b3f7c02e91a4 refuses to delete
        # category 15 while any review row references it. This endpoint offered the same state
        # change with no such check - and it is not owner-scoped, so one toggle reaches everyone.
        if not active and category.active:
            in_use = session.scalar(
                select(func.count()).select_from(ReviewRow).where(ReviewRow.category == category_id)
            )
            if in_use:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"category {category_id} is used by {in_use} sub-document"
                        f"{'' if in_use == 1 else 's'} and cannot be deactivated. Move those rows "
                        "to another category first."
                    ),
                )
        category.active = active  # active=False is the soft-delete
    session.commit()
    catalog.bump_revision(session)
    audit(session, "category.update", user.id)
    return _category_payload(session, category)


@router.get("/prompts/{category_id}")
def get_summary_prompt(category_id: str, session: Session = Depends(get_db)):
    row = session.scalar(
        select(Prompt).where(Prompt.role == "summary", Prompt.category_id == category_id)
    )
    return {
        "category_id": category_id,
        "text": row.text if row is not None else None,
        "effective_text": catalog.get_prompt(session, "summary", category_id),
        # What a revert would restore: this category's prompt from the app code. Surfaced so the
        # dialog can show the built-in beside a custom prompt instead of the general one, which is
        # no longer what an un-customized category uses.
        "builtin_text": catalog.builtin_summary_prompt(session, category_id),
        "custom": row is not None,
    }


@router.put("/prompts/{category_id}")
def put_summary_prompt(
    category_id: str,
    payload: PromptPut,
    session: Session = Depends(get_db),
    user: User = Depends(current_superuser),
):
    seed_categories(session)  # same reason as update_category: a built-in has no row to attach to
    if session.get(Category, category_id) is None:
        raise HTTPException(status_code=404, detail="unknown category")
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="prompt text cannot be empty")

    row = session.scalar(
        select(Prompt).where(Prompt.role == "summary", Prompt.category_id == category_id)
    )
    if row is None:
        session.add(Prompt(role="summary", category_id=category_id, text=text, revision=1))
    else:
        row.text = text
        row.revision += 1
    session.commit()
    catalog.bump_revision(session)
    audit(session, "prompt.update", user.id)
    return {"category_id": category_id, "text": text, "custom": True}


@router.delete("/prompts/{category_id}")
def delete_summary_prompt(
    category_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(current_superuser),
):
    """Revert a category to its built-in prompt by dropping the custom row.

    Deleting IS the mechanism: with no row of its own, catalog.get_prompt resolves this category's
    prompt from prompts.py, so it tracks the deployed code again.
    """
    row = session.scalar(
        select(Prompt).where(Prompt.role == "summary", Prompt.category_id == category_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="this category has no custom prompt")
    session.delete(row)
    session.commit()
    catalog.bump_revision(session)
    audit(session, "prompt.revert", user.id)
    return {
        "category_id": category_id,
        "text": None,
        "effective_text": catalog.get_prompt(session, "summary", category_id),
        "custom": False,
    }


@router.post("/reprocess/{document_id}")
def reprocess(
    document_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(current_superuser),
):
    """Re-summarize a document with the CURRENT prompts (admin-scoped: any owner's document), so an
    admin can apply a prompt/category edit to existing records. Reuses the summarize enqueue."""
    document = session.get(Document, document_id)  # no owner filter: admin acts on any document
    if document is None:
        raise HTTPException(status_code=404, detail="not found")
    if not any(row.include for row in document.review_rows):
        raise HTTPException(status_code=400, detail="no reviewed rows to summarize")
    try:
        enqueue(
            session,
            document.id,
            "summarize",
            model=get_settings().summary_model,
            prompt_version=PROMPT_VERSION,
            catalog_revision=catalog.catalog_version(session),
        )
    except JobConflict:
        raise HTTPException(status_code=409, detail="a job is already running for this document")
    audit(session, "reprocess", user.id, document.id)
    return {"ok": True}
