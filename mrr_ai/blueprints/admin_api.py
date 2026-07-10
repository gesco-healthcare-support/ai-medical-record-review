"""Admin JSON API (category + prompt administration).

Every route here is under /api/admin, so the app-level auth gate in ``security.py`` already
requires an authenticated ``is_admin`` account before any handler runs - the handlers never
re-check authorization themselves.

Every successful edit bumps the catalog revision (invalidating the classifier's caches and
stamping subsequent jobs) and writes an audit row. Category ids are immutable: they key stored
review rows, so an edit never changes an id (a deactivation soft-deletes instead).
"""

import re

from flask import Blueprint, jsonify, request
from flask_security import current_user

from mrr_ai import catalog
from mrr_ai.extensions import db
from mrr_ai.models import Category, Prompt
from mrr_ai.services.audit import audit

bp = Blueprint("admin_api", __name__, url_prefix="/api/admin")

_ID_RE = re.compile(r"^\d+$")


@bp.get("/whoami")
def whoami():
    """Confirm the caller reached the admin area as an admin (used by the UI + tests)."""
    return jsonify({"email": current_user.email, "is_admin": bool(current_user.is_admin)})


def _category_payload(category):
    data = category.listing()
    data["has_summary_prompt"] = (
        Prompt.query.filter_by(role="summary", category_id=category.id).first() is not None
    )
    return data


@bp.get("/categories")
def list_categories():
    """All categories (including inactive) sorted by numeric id, for the admin table."""
    categories = sorted(Category.query.all(), key=lambda c: int(c.id))
    return jsonify([_category_payload(c) for c in categories])


@bp.post("/categories")
def create_category():
    body = request.get_json(silent=True) or {}
    category_id = str(body.get("id", "")).strip()
    name = (body.get("name") or "").strip()
    examples = body.get("examples", [])
    if not _ID_RE.match(category_id):
        return jsonify({"error": "category id must be a positive number"}), 400
    if db.session.get(Category, category_id) is not None:
        return jsonify({"error": f"category {category_id} already exists"}), 400
    if not name:
        return jsonify({"error": "name is required"}), 400
    if not isinstance(examples, list):
        return jsonify({"error": "examples must be a list"}), 400

    category = Category(
        id=category_id,
        name=name,
        description=(body.get("description") or "").strip(),
        examples=examples,
        active=bool(body.get("active", True)),
        auto_assign=bool(body.get("auto_assign", True)),
    )
    db.session.add(category)
    db.session.commit()
    catalog.bump_revision()
    audit("category.create")
    return jsonify(_category_payload(category)), 201


@bp.patch("/categories/<category_id>")
def update_category(category_id):
    category = db.session.get(Category, category_id)
    if category is None:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    # ``id`` is intentionally NOT read from the body - ids are immutable.
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            return jsonify({"error": "name cannot be empty"}), 400
        category.name = name
    if "description" in body:
        category.description = (body["description"] or "").strip()
    if "examples" in body:
        if not isinstance(body["examples"], list):
            return jsonify({"error": "examples must be a list"}), 400
        category.examples = body["examples"]
    if "auto_assign" in body:
        category.auto_assign = bool(body["auto_assign"])
    if "active" in body:
        category.active = bool(body["active"])
    db.session.commit()
    catalog.bump_revision()
    audit("category.update")
    return jsonify(_category_payload(category))


@bp.get("/prompts/<category_id>")
def get_summary_prompt(category_id):
    """The category's own summary prompt (``text``), plus the ``effective_text`` actually used
    (the general prompt when the category has none)."""
    row = Prompt.query.filter_by(role="summary", category_id=category_id).first()
    return jsonify(
        {
            "category_id": category_id,
            "text": row.text if row is not None else None,
            "effective_text": catalog.get_prompt("summary", category_id),
            "custom": row is not None,
        }
    )


@bp.put("/prompts/<category_id>")
def put_summary_prompt(category_id):
    if db.session.get(Category, category_id) is None:
        return jsonify({"error": "unknown category"}), 404
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "prompt text cannot be empty"}), 400

    row = Prompt.query.filter_by(role="summary", category_id=category_id).first()
    if row is None:
        db.session.add(Prompt(role="summary", category_id=category_id, text=text, revision=1))
    else:
        row.text = text
        row.revision += 1
    db.session.commit()
    catalog.bump_revision()
    audit("prompt.update")
    return jsonify({"category_id": category_id, "text": text, "custom": True})
