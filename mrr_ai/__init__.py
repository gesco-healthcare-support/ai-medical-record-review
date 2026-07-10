"""MRR AI application package (Flask application factory)."""

import mimetypes
import os

from flask import Flask

from mrr_ai.config import (
    MAX_CONTENT_LENGTH,
    SECRET_KEY,
    SECURITY_PASSWORD_SALT,
    SQLALCHEMY_DATABASE_URI,
    UPLOAD_FOLDER,
)

# Windows' registry-backed mimetypes lacks .mjs, so Flask would serve the vendored
# PDF.js ES modules as text/plain - and browsers refuse module scripts with a
# non-JavaScript MIME type (strict checking per the HTML spec).
mimetypes.add_type("text/javascript", ".mjs")


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

    from mrr_ai.cli import admin_cli

    app.cli.add_command(admin_cli)

    _create_schema(app)

    # Start the classifier's catalog cache clean: it lazily loads the (just-seeded) DB
    # catalog on first use, and this keeps one app's cache from leaking into another.
    from mrr_ai.services import classification

    classification.reset_catalog_cache()

    from mrr_ai.services import job_queue

    job_queue.init_app(app)  # after the schema exists: init sweeps orphaned jobs
    return app


# Additive columns introduced after the first databases shipped. create_all cannot
# ALTER existing tables, and real data (the seeded demo DB) must survive upgrades.
# This stopgap covers ADD COLUMN only; anything harder (renames, drops, backfills)
# is the trigger to introduce Alembic properly.
_ADDITIVE_COLUMNS = {
    "user": [("name", "VARCHAR(255)"), ("is_admin", "BOOLEAN NOT NULL DEFAULT 0")],
    "review_rows": [("include", "BOOLEAN NOT NULL DEFAULT 1")],
    "summaries": [
        ("edited_title", "VARCHAR(512)"),
        ("edited_date", "VARCHAR(16)"),
        ("edited_text", "TEXT"),
        ("excluded", "BOOLEAN NOT NULL DEFAULT 0"),
        ("source_text", "TEXT"),
    ],
}


def _create_schema(app):
    """Create tables on boot, then apply additive column migrations."""
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("sqlite:///"):
        directory = os.path.dirname(uri.removeprefix("sqlite:///"))
        if directory:
            os.makedirs(directory, exist_ok=True)

    from sqlalchemy import text

    from mrr_ai import models  # noqa: F401 - models must be imported before create_all
    from mrr_ai.extensions import db

    with app.app_context():
        db.create_all()
        for table, columns in _ADDITIVE_COLUMNS.items():
            existing = {
                row[1] for row in db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
            }
            for name, ddl in columns:
                if name not in existing:
                    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        db.session.commit()

        # Populate the editable category/prompt catalog from the constants (idempotent).
        from mrr_ai.seed_catalog import seed_catalog

        seed_catalog(db)
