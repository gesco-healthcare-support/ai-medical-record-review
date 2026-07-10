"""DB-first accessor for the editable category / prompt catalog.

The category catalog and per-category summary prompts live in DB tables seeded from the
constants in ``taxonomy.py`` / ``prompts.py``. These helpers read the DB and fall back to the
constants if the tables are unseeded or a prompt row is missing, so behavior is identical to
the pre-feature code when nothing has been edited. Callers already run inside an app context;
SQLite reads are cheap, so nothing is cached here - the expensive classifier caches invalidate
off ``catalog_version()`` separately.
"""

from mrr_ai.extensions import db
from mrr_ai.models import CatalogMeta, Category, Prompt
from mrr_ai.seed_catalog import constants_categories, constants_summary_prompt


def _by_id(categories):
    return sorted(categories, key=lambda category: int(category["id"]))


def get_categories(active_only=False, auto_assign=False):
    """Category dicts sorted by numeric id.

    ``auto_assign=True`` -> the classifier's assignable set (active AND auto_assign).
    ``active_only=True``  -> the editor's selectable set (all active, e.g. including id 6).
    Neither flag -> every category (for the admin UI, which shows inactive ones too).
    """
    rows = Category.query.all()
    categories = [row.listing() for row in rows] if rows else constants_categories()
    if auto_assign:
        categories = [c for c in categories if c["active"] and c["auto_assign"]]
    elif active_only:
        categories = [c for c in categories if c["active"]]
    return _by_id(categories)


def get_category_ids(active_only=True, auto_assign=False):
    """Just the ids from :func:`get_categories`."""
    return [c["id"] for c in get_categories(active_only=active_only, auto_assign=auto_assign)]


def get_category_options(active_only=True):
    """``[{id, name}]`` for the review editor's category dropdown + labels (active only)."""
    return [{"id": c["id"], "name": c["name"]} for c in get_categories(active_only=active_only)]


def get_prompt(role, category_id):
    """The current prompt text for ``(role, category_id)``, DB-first.

    For summaries, a category with no prompt row falls back to the general (100) prompt,
    mirroring the engine's historical ``prompts.get(key, category_100)`` behavior, and the
    constants back-stop it when the DB is unseeded.
    """
    category_id = str(category_id)
    row = Prompt.query.filter_by(role=role, category_id=category_id).first()
    if row is not None:
        return row.text
    if role == "summary":
        general = Prompt.query.filter_by(role="summary", category_id="100").first()
        return general.text if general is not None else constants_summary_prompt(category_id)
    return None


def catalog_version():
    """Monotonic revision of the catalog; bumped on any category/prompt edit."""
    meta = db.session.get(CatalogMeta, 1)
    return meta.revision if meta is not None else 0


def bump_revision():
    """Increment the catalog revision and commit. Callers invoke this after any category or
    prompt edit so the classifier's cached catalog/matrix invalidate and new jobs stamp the
    new version. Returns the new revision."""
    meta = db.session.get(CatalogMeta, 1)
    if meta is None:
        meta = CatalogMeta(id=1, revision=1)
        db.session.add(meta)
    else:
        meta.revision += 1
    db.session.commit()
    return meta.revision
