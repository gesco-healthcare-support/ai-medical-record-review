"""Seed the editable Category / Prompt / CatalogMeta tables from the hardcoded constants.

Runs on every boot (from ``_create_schema``) but only populates when the ``categories`` table
is empty, so an already-seeded (or admin-edited) database is never overwritten. The Python
constants in ``taxonomy.py`` / ``prompts.py`` remain the seed source AND the runtime fallback
(``catalog.py`` reuses the ``constants_*`` helpers here when a DB row is missing).
"""

# Id 6 exists only downstream today (a prompt + an editor label, no taxonomy entry): it is
# manually selectable in the review editor but never auto-assigned by the classifier.
_ID_SIX = {
    "id": "6",
    "name": "Daily / SOAP notes",
    "description": "Daily encounter and SOAP notes.",
    "examples": [],
    "active": True,
    "auto_assign": False,
}


def _prompt_key(category_id):
    """The legacy prompts-dict key for a category id (e.g. '3' -> 'category_03')."""
    return f"category_{int(category_id):02d}"


def constants_categories():
    """The canonical category catalog as dicts, from the constants (taxonomy ids + id 6).

    Shape matches ``models.Category.listing()`` so it can back-fill the DB accessor when the
    catalog tables are unseeded.
    """
    from mrr_ai.taxonomy import CATEGORIES

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


def constants_summary_prompt(category_id):
    """The hardcoded summary prompt for a category id, with the general (100) fallback."""
    from mrr_ai.prompts import prompts as prompt_texts

    return prompt_texts.get(_prompt_key(category_id), prompt_texts["category_100"])


def seed_catalog(db):
    """Populate the catalog tables from the constants if not already seeded (idempotent)."""
    from mrr_ai.models import CatalogMeta, Category, Prompt
    from mrr_ai.prompts import prompts as prompt_texts
    from mrr_ai.taxonomy import CATEGORIES

    if Category.query.first() is not None:
        return  # already seeded (or edited) - never clobber existing rows

    for category in constants_categories():
        db.session.add(Category(**category))

    # One summary prompt per category id that has a hardcoded prompt today. Id 11 has none, so
    # it is intentionally left without a row and falls back to the general (100) prompt.
    for category_id in [*CATEGORIES.keys(), "6"]:
        text = prompt_texts.get(_prompt_key(category_id))
        if text is not None:
            db.session.add(Prompt(role="summary", category_id=category_id, text=text, revision=1))

    if db.session.get(CatalogMeta, 1) is None:
        db.session.add(CatalogMeta(id=1, revision=1))

    db.session.commit()
