"""Authentication: Flask-Security wiring + a deny-by-default request gate.

The gate lives at the app level (not per-route decorators) so a newly added route can
never ship unprotected: everything outside PUBLIC_ENDPOINTS requires an authenticated
session. Flask-Security's unauthorized handler content-negotiates - browser requests
redirect to /login, JSON requests get 401 JSON - so the fetch()-based APIs need no
special casing here.
"""

import re

from flask import request
from flask_security import PasswordUtil, Security, SQLAlchemyUserDatastore, current_user
from flask_wtf import CSRFProtect

# Endpoints reachable without a session. Everything else is denied by default.
PUBLIC_ENDPOINTS = {"security.login", "security.register", "static"}


class MrrPasswordUtil(PasswordUtil):
    """Server-side twin of the registration checklist (8+ characters via the stock
    length validator, plus a number and a symbol) so a direct API POST can never
    register a password the UI would reject. Applies wherever Flask-Security
    validates a NEW password (register, change, reset) - never at login."""

    def validate(self, password, is_register, **kwargs):
        messages, normalized = super().validate(password, is_register, **kwargs)
        extra = []
        if not re.search(r"\d", normalized):
            extra.append("Password must contain a number")
        if not re.search(r"[^A-Za-z0-9]", normalized):
            extra.append("Password must contain a symbol")
        if extra:
            messages = (messages or []) + extra
        return messages, normalized


def init_security(app):
    """Wire CSRF + Flask-Security + the global auth gate onto ``app``."""
    # Official Flask-Security pattern: CSRFProtect must initialize BEFORE Security so
    # the XSRF-TOKEN cookie / X-XSRF-Token header flow covers every unsafe request,
    # including our own JSON endpoints.
    CSRFProtect(app)

    from mrr_ai.extensions import db
    from mrr_ai.models import Role, User

    datastore = SQLAlchemyUserDatastore(db, User, Role)
    security = Security(app, datastore, password_util_cls=MrrPasswordUtil)

    @app.before_request
    def _require_authentication():
        endpoint = request.endpoint
        # endpoint None = no URL match; let Flask 404 (reveals nothing to probes).
        if endpoint is None or endpoint in PUBLIC_ENDPOINTS:
            return None
        if current_user.is_authenticated:
            return None
        return app.login_manager.unauthorized()

    return security
