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


# The fields a category edit can move, split by whether the trail may echo their VALUE.
#
# The flags decide BEHAVIOUR - whether the classifier may pick the category, whether its
# documents are summarized by default, and whether the category exists for `validate_rows` at
# all - and they are booleans, so `old -> new` is the whole story and carries nothing but an
# enum value.
#
# name, description and examples are admin-typed FREE TEXT. `AuditLog.detail` is ids and enum
# values only, so those are reported as having CHANGED without quoting them: "name changed"
# tells a reader everything they need in order to go and look, and echoing arbitrary typed
# text into an append-only trail buys nothing.
_CATEGORY_FLAG_FIELDS = ("active", "auto_assign", "summarize_default")
_CATEGORY_TEXT_FIELDS = ("name", "description", "examples")
_CATEGORY_FIELDS = _CATEGORY_FLAG_FIELDS + _CATEGORY_TEXT_FIELDS


def _category_update_detail(category_id: str, before: dict, after: dict) -> str:
    """What a category PATCH actually moved, for the audit trail.

    Compares VALUES rather than listing the fields the request carried, because the admin
    dialog sends all six on every save (`category-dialog.tsx` builds a full `CategoryInput`) -
    so a detail keyed on which fields were PRESENT would report every edit as changing
    everything, and say no more than the action name already does.

    "no change" is deliberate rather than an empty detail. Opening a category and confirming
    it is a different fact from nobody having looked at it, and `updated_at` moves either way,
    so the trail is the only thing that can separate them - the same argument as
    `_rows_edit_detail` recording zero counts for a row save that changed nothing.
    """
    moved = [
        f"{field} {before[field]} -> {after[field]}"
        for field in _CATEGORY_FLAG_FIELDS
        if field in before and before[field] != after[field]
    ]
    moved += [
        f"{field} changed"
        for field in _CATEGORY_TEXT_FIELDS
        if field in before and before[field] != after[field]
    ]
    return f"category {category_id}: {', '.join(moved) if moved else 'no change'}"


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


@router.post(
    "/categories",
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "description": "The id is not a positive number, the name is empty, "
            "or a category with that id already exists."
        }
    },
)
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
    audit(
        session,
        "category.create",
        user.id,
        detail=f"category {category_id} active {category.active} "
        f"auto_assign {category.auto_assign} summarize_default {category.summarize_default}",
    )
    return _category_payload(session, category)


def _apply_category_edits(category: Category, body: dict) -> None:
    """Assign the five plain editable fields present in ``body``. Raises 400 on an empty name.

    Deliberately does NOT handle ``active``: deactivation carries an in-use check that must run
    AFTER these assignments, because the 400 for an empty name has to win over the 409 for a
    category in use. A single PATCH carrying both a blank name and active=false returns 400 today,
    and that ordering is the only thing keeping it that way.

    Defined ABOVE the route decorator on purpose. A helper placed between a decorator and its
    function silently steals the decorator - FastAPI then tries to build a request model from this
    signature and the real route never registers.
    """
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


@router.patch(
    "/categories/{category_id}",
    responses={
        400: {"description": "The name is empty."},
        404: {"description": "No category has this id."},
        409: {
            "description": "The category is still used by sub-documents, so it cannot be "
            "deactivated until those rows move to another category."
        },
    },
)
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
    # Snapshot BEFORE the walk below mutates the row: the audit detail reports what MOVED, and
    # once the fields are assigned there is nothing left to compare them against.
    before = {field: getattr(category, field) for field in _CATEGORY_FIELDS if field in body}
    _apply_category_edits(category, body)
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
    audit(
        session,
        "category.update",
        user.id,
        detail=_category_update_detail(
            category_id, before, {field: getattr(category, field) for field in _CATEGORY_FIELDS}
        ),
    )
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


@router.put(
    "/prompts/{category_id}",
    responses={
        400: {"description": "The prompt text is empty."},
        404: {"description": "No category has this id."},
    },
)
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
        change = "created"
    else:
        previous = row.revision
        row.text = text
        row.revision += 1
        change = f"revision {previous} -> {row.revision}"
    session.commit()
    catalog.bump_revision(session)
    # A custom prompt overrides the built-in for every summary written in this category until
    # it is reverted, so the trail has to name the category and say how far it has diverged.
    # The length rather than the text: prompt bodies are app content and not PHI, but the row
    # already holds the text, and what a reader wants from a trail is which category changed.
    audit(
        session,
        "prompt.update",
        user.id,
        detail=f"category {category_id} prompt {change}, {len(text)} chars",
    )
    return {"category_id": category_id, "text": text, "custom": True}


@router.delete(
    "/prompts/{category_id}",
    responses={404: {"description": "This category has no custom prompt to delete."}},
)
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
    # Read the revision and the size BEFORE the delete. Reverting DISCARDS the custom text with
    # no copy kept anywhere, so once this row is gone the audit row is the only remaining
    # record that the category ever had a custom prompt at all - while summaries written under
    # it still carry its fingerprint in `prompt_version`, which now resolves to nothing.
    discarded = f"revision {row.revision}, {len(row.text or '')} chars"
    session.delete(row)
    session.commit()
    catalog.bump_revision(session)
    audit(
        session,
        "prompt.revert",
        user.id,
        detail=f"category {category_id} prompt dropped: {discarded}",
    )
    return {
        "category_id": category_id,
        "text": None,
        "effective_text": catalog.get_prompt(session, "summary", category_id),
        "custom": False,
    }


@router.post(
    "/reprocess/{document_id}",
    responses={
        400: {"description": "The document has no reviewed rows to summarize."},
        404: {"description": "No document has this id."},
        409: {"description": "A job is already running for this document."},
    },
)
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
