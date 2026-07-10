"""Admin JSON API (category + prompt administration).

Every route here is under /api/admin, so the app-level auth gate in ``security.py``
already requires an authenticated ``is_admin`` account before any handler runs - the
handlers never re-check authorization themselves.

Phase 1 starts with a ``whoami`` probe; category and prompt CRUD land in later tasks.
"""

from flask import Blueprint, jsonify
from flask_security import current_user

bp = Blueprint("admin_api", __name__, url_prefix="/api/admin")


@bp.get("/whoami")
def whoami():
    """Confirm the caller reached the admin area as an admin (used by the UI + tests)."""
    return jsonify({"email": current_user.email, "is_admin": bool(current_user.is_admin)})
