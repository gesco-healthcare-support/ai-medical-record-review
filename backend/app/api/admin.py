"""Admin JSON API: category + prompt administration + admin reprocess (ported from admin_api).

All routes are under /api/admin, protected by the app-level auth gate (P2b: 403 for non-superusers)
AND a per-route superuser dependency here (defense in depth). Every successful edit bumps the
catalog revision - which invalidates the worker classifier caches (they poll catalog_version()) and
stamps subsequent jobs - and writes an audit row. Category ids are immutable; deactivation
soft-deletes. `reprocess` is admin-scoped: it acts on ANY owner's document (no owner filter).
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_superuser
from app.config import get_settings
from app.db import get_db
from app.models import Category, Document, Prompt, User
from app.schemas.admin import CategoryCreate, CategoryUpdate, PromptPut
from app.services import catalog
from app.services.audit import audit
from app.services.gemini import PROMPT_VERSION
from app.services.jobs import JobConflict, enqueue

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_superuser)])

_ID_RE = re.compile(r"^\d+$")


def _category_payload(session: Session, category: Category) -> dict:
    data = category.listing()
    data["has_summary_prompt"] = (
        session.scalar(
            select(Prompt).where(Prompt.role == "summary", Prompt.category_id == category.id)
        )
        is not None
    )
    return data


@router.get("/whoami")
def whoami(user: User = Depends(current_superuser)):
    return {"email": user.email, "is_admin": bool(user.is_superuser)}


@router.get("/categories")
def list_categories(session: Session = Depends(get_db)):
    categories = sorted(session.scalars(select(Category)).all(), key=lambda c: int(c.id))
    return [_category_payload(session, c) for c in categories]


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
    if session.get(Category, category_id) is not None:
        raise HTTPException(status_code=400, detail=f"category {category_id} already exists")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    category = Category(
        id=category_id,
        name=name,
        description=(payload.description or "").strip(),
        examples=payload.examples,
        active=payload.active,
        auto_assign=payload.auto_assign,
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
    if "active" in body:
        category.active = bool(body["active"])  # active=False is the soft-delete
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
        "custom": row is not None,
    }


@router.put("/prompts/{category_id}")
def put_summary_prompt(
    category_id: str,
    payload: PromptPut,
    session: Session = Depends(get_db),
    user: User = Depends(current_superuser),
):
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
