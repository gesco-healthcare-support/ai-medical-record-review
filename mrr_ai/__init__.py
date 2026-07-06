"""MRR AI application package (Flask application factory)."""

import os

from flask import Flask

from mrr_ai.config import (
    MAX_CONTENT_LENGTH,
    SECRET_KEY,
    SECURITY_PASSWORD_SALT,
    SQLALCHEMY_DATABASE_URI,
    UPLOAD_FOLDER,
)


def create_app(config_overrides=None):
    """Build the Flask app: config, DB, auth, blueprints, schema.

    ``config_overrides`` lets tests inject an isolated database / CSRF settings
    before any extension initializes against the config.
    """
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
        UPLOAD_FOLDER=UPLOAD_FOLDER,
        SECRET_KEY=SECRET_KEY,
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI,
        # Session-cookie hardening. SESSION_COOKIE_SECURE must be added the day the
        # app is served over HTTPS; setting it now would break plain-HTTP LAN use.
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Flask-Security 5.8 (keys verified against the official docs; see the plan's
        # Appendix A). Registration keeps the confirm-password field by default.
        SECURITY_PASSWORD_SALT=SECURITY_PASSWORD_SALT,
        SECURITY_REGISTERABLE=True,
        SECURITY_CONFIRMABLE=False,  # no SMTP on this box yet; flip when mail exists
        SECURITY_SEND_REGISTER_EMAIL=False,  # welcome email needs the same missing SMTP
        # Syntax-only email validation: the deliverability check does a live DNS/MX
        # lookup per registration - a network dependency this LAN-internal box does
        # not need, and it rejects reserved test domains in the suite.
        SECURITY_EMAIL_VALIDATOR_ARGS={"check_deliverability": False},
        SECURITY_POST_LOGIN_VIEW="/",
        SECURITY_POST_REGISTER_VIEW="/",
        SECURITY_POST_LOGOUT_VIEW="/login",
        # CSRF for the fetch()-based APIs: cookie out, X-XSRF-Token header back in.
        SECURITY_CSRF_COOKIE_NAME="XSRF-TOKEN",
        # Tokens live as long as the session; they are invalidated at logout anyway,
        # and a mid-review expiry would eat a user's row edits.
        WTF_CSRF_TIME_LIMIT=None,
    )
    if config_overrides:
        app.config.update(config_overrides)

    from mrr_ai.extensions import db

    db.init_app(app)

    from mrr_ai.security import init_security

    init_security(app)

    from mrr_ai.blueprints import register_blueprints

    register_blueprints(app)

    _create_schema(app)

    from mrr_ai.services import job_queue

    job_queue.init_app(app)  # after the schema exists: init sweeps orphaned jobs
    return app


def _create_schema(app):
    """Create tables on boot.

    create_all is additive-only: it cannot ALTER existing tables, so the first
    post-release schema CHANGE must introduce Alembic (tracked in the plan).
    """
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("sqlite:///"):
        directory = os.path.dirname(uri.removeprefix("sqlite:///"))
        if directory:
            os.makedirs(directory, exist_ok=True)

    from mrr_ai import models  # noqa: F401 - models must be imported before create_all
    from mrr_ai.extensions import db

    with app.app_context():
        db.create_all()
