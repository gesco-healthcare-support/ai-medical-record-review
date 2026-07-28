"""DB-first accessor for the editable category / prompt catalog (session-based).

Reads the DB catalog and falls back to the taxonomy/prompts constants when a table is unseeded
or a prompt row is missing, so behavior matches the pre-feature code. Every accessor takes an
explicit Session (the Flask version used the request-scoped db.session) - the plan's "one crack"
where a service reads the DB. The classifier's caches invalidate off catalog_version() separately.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatalogMeta, Category, Prompt
from app.services.seed_catalog import (
    code_summary_prompt,
    constants_categories,
    constants_summary_prompt,
)


def _by_id(categories: list[dict]) -> list[dict]:
    return sorted(categories, key=lambda category: int(category["id"]))


def get_categories(session: Session, active_only: bool = False, auto_assign: bool = False):
    """Category dicts sorted by numeric id.

    auto_assign=True -> the classifier's assignable set (active AND auto_assign).
    active_only=True  -> the editor's selectable set (all active, e.g. including id 6).
    Neither -> every category (the admin UI shows inactive ones too).
    """
    rows = session.scalars(select(Category)).all()
    categories = [row.listing() for row in rows] if rows else constants_categories()
    if auto_assign:
        categories = [c for c in categories if c["active"] and c["auto_assign"]]
    elif active_only:
        categories = [c for c in categories if c["active"]]
    return _by_id(categories)


def get_category_ids(session: Session, active_only: bool = True, auto_assign: bool = False):
    return [
        c["id"] for c in get_categories(session, active_only=active_only, auto_assign=auto_assign)
    ]


def get_category_options(session: Session, active_only: bool = True):
    """[{id, name}] for the review editor's category dropdown + labels (active only)."""
    return [
        {"id": c["id"], "name": c["name"]} for c in get_categories(session, active_only=active_only)
    ]


def summarize_default_for(session: Session, category_id) -> bool:
    """Whether this category is checked for summarization by default (DB-first, constants fallback).

    Only explicitly-off categories (General, Depositions) return False; an unknown id defaults to
    True (include), so a new/unseeded category is never silently excluded.
    """
    category_id = str(category_id)
    for category in get_categories(session):
        if category["id"] == category_id:
            return bool(category.get("summarize_default", True))
    return True


def get_prompt(session: Session, role: str, category_id) -> str | None:
    """The current prompt text for (role, category_id).

    Resolution order for summaries, most specific first:
      1. this category's own prompt row  - an admin edit, which always wins;
      2. this category's own CODE prompt  - prompts.py is the source of truth, so a deployed prompt
         change reaches every box;
      3. the general (100) prompt row     - only for a category the code has no prompt for (11);
      4. the general code prompt          - back-stop on an unseeded DB.
    Step 2 must come before step 3: otherwise a category with no row of its own is summarized with
    the catch-all prompt rather than its own rules.
    """
    category_id = str(category_id)
    row = session.scalar(
        select(Prompt).where(Prompt.role == role, Prompt.category_id == category_id)
    )
    if row is not None:
        return row.text
    if role != "summary":
        return None
    return builtin_summary_prompt(session, category_id)


def builtin_summary_prompt(session: Session, category_id) -> str:
    """The summary prompt a category resolves to when it has NO custom row of its own - steps 2-4 of
    get_prompt's chain. The admin UI shows this as "the built-in prompt" so a reviewer can see what
    reverting would restore."""
    category_id = str(category_id)
    code_prompt = code_summary_prompt(category_id)
    if code_prompt is not None:
        return code_prompt
    general = session.scalar(
        select(Prompt).where(Prompt.role == "summary", Prompt.category_id == "100")
    )
    return general.text if general is not None else constants_summary_prompt(category_id)


def catalog_version(session: Session) -> int:
    """Monotonic revision of the catalog; bumped on any category/prompt edit."""
    meta = session.get(CatalogMeta, 1)
    return meta.revision if meta is not None else 0


def bump_revision(session: Session) -> int:
    """Increment the catalog revision and commit; invalidates classifier caches + stamps jobs."""
    meta = session.get(CatalogMeta, 1)
    if meta is None:
        meta = CatalogMeta(id=1, revision=1)
        session.add(meta)
    else:
        meta.revision += 1
    session.commit()
    return meta.revision
