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
}


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
        }
        for category in CATEGORIES.values()
    ]
    categories.append(dict(_ID_SIX))
    return categories


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
