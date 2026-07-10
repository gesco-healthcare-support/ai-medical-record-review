"""Seed the editable Category / Prompt / CatalogMeta tables from the hardcoded constants.

Runs on every boot (from ``_create_schema``) but only populates when the ``categories`` table
is empty, so an already-seeded (or admin-edited) database is never overwritten. The Python
constants in ``taxonomy.py`` / ``prompts.py`` remain the seed source and the runtime fallback.
"""


def _prompt_key(category_id):
    """The legacy prompts-dict key for a category id (e.g. '3' -> 'category_03')."""
    return f"category_{int(category_id):02d}"


def seed_catalog(db):
    """Populate the catalog tables from the constants if not already seeded (idempotent)."""
    from mrr_ai.models import CatalogMeta, Category, Prompt
    from mrr_ai.prompts import prompts as prompt_texts
    from mrr_ai.taxonomy import CATEGORIES

    if Category.query.first() is not None:
        return  # already seeded (or edited) - never clobber existing rows

    # Taxonomy categories are the classifier's auto-assignable set.
    for category in CATEGORIES.values():
        db.session.add(
            Category(
                id=category.id,
                name=category.name,
                description=category.description,
                examples=list(category.examples),
                active=True,
                auto_assign=True,
            )
        )
    # Id 6 exists only downstream today (a prompt + an editor label, no taxonomy entry): it is
    # manually selectable in the review editor but never auto-assigned by the classifier.
    db.session.add(
        Category(
            id="6",
            name="Daily / SOAP notes",
            description="Daily encounter and SOAP notes.",
            examples=[],
            active=True,
            auto_assign=False,
        )
    )

    # One summary prompt per category id that has a hardcoded prompt today. Id 11 has none, so
    # it is intentionally left without a row and falls back to the general (100) prompt.
    for category_id in [*CATEGORIES.keys(), "6"]:
        text = prompt_texts.get(_prompt_key(category_id))
        if text is not None:
            db.session.add(Prompt(role="summary", category_id=category_id, text=text, revision=1))

    if db.session.get(CatalogMeta, 1) is None:
        db.session.add(CatalogMeta(id=1, revision=1))

    db.session.commit()
