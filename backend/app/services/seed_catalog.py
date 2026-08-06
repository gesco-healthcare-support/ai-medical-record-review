"""Seed / fallback constants for the editable Category / Prompt / CatalogMeta catalog.

The taxonomy.py / prompts.py constants are both the seed source AND the runtime fallback
(catalog.py reuses constants_* when a DB row is missing), so behavior matches the pre-feature
code when nothing has been edited. seed_catalog() populates a fresh DB idempotently.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.prompts import prompts as prompt_texts
from app.services.taxonomy import CATEGORIES

# Id 6 exists only downstream (a prompt + an editor label, no taxonomy entry): manually
# selectable in the review editor but never auto-assigned by the classifier.
_ID_SIX = {
    "id": "6",
    "name": "Daily / SOAP notes",
    "description": "Daily encounter and SOAP notes.",
    "examples": [],
    "active": True,
    "auto_assign": False,
    "summarize_default": True,
}

# Categories unchecked for summarization by default (rarely summarized): General.
#
# Depositions (9) were here until 2026-08-06. Adrian turned them on: a reviewer had to remember a
# switch, so the deposition prompt reached almost no delivered output.
#
# NOTE this constant only affects a BRAND-NEW database - seed_catalog() returns early once any Category
# row exists ("already seeded (or edited)"), so editing it alone ships NOTHING to the server or to a
# developer's stack. Migration a9c4e13f70b2 carries the change to existing boxes. Same trap as
# f1a83b5c60d2, which exists because a prompt edit in code changed nothing on a box that was already
# seeded.
_SUMMARIZE_OFF_BY_DEFAULT = {"100"}


def _prompt_key(category_id) -> str:
    """The legacy prompts-dict key for a category id (e.g. '3' -> 'category_03')."""
    return f"category_{int(category_id):02d}"


def constants_categories() -> list[dict]:
    """The canonical category catalog as dicts (taxonomy ids + id 6); shape matches
    Category.listing() so it can back-fill the DB accessor when the tables are unseeded."""
    categories = [
        {
            "id": category.id,
            "name": category.name,
            "description": category.description,
            "examples": list(category.examples),
            "active": True,
            "auto_assign": True,
            "summarize_default": category.id not in _SUMMARIZE_OFF_BY_DEFAULT,
        }
        for category in CATEGORIES.values()
    ]
    categories.append(dict(_ID_SIX))
    return categories


def code_summary_prompt(category_id) -> str | None:
    """The hardcoded summary prompt for THIS category id, or None when the code defines none.

    Distinct from constants_summary_prompt, which falls back to the general prompt: callers that
    must ask "does the code own a prompt for this category?" (prompt resolution) need the
    unsubstituted answer, or every category looks like it has one.
    """
    return prompt_texts.get(_prompt_key(category_id))


def constants_summary_prompt(category_id) -> str:
    """The hardcoded summary prompt for a category id, with the general (100) fallback."""
    return prompt_texts.get(_prompt_key(category_id), prompt_texts["category_100"])


def seed_catalog(session: Session) -> None:
    """Populate the catalog tables from the constants if empty (idempotent; never clobbers)."""
    from app.models import CatalogMeta, Category, Prompt

    if session.scalar(select(Category)) is not None:
        return  # already seeded (or edited)
    for category in constants_categories():
        session.add(Category(**category))
    # One summary prompt per category id with a hardcoded prompt. Id 11 has none -> no row ->
    # falls back to the general (100) prompt.
    for category_id in [*CATEGORIES.keys(), "6"]:
        text = prompt_texts.get(_prompt_key(category_id))
        if text is not None:
            session.add(Prompt(role="summary", category_id=category_id, text=text, revision=1))
    if session.get(CatalogMeta, 1) is None:
        session.add(CatalogMeta(id=1, revision=1))
    session.commit()
