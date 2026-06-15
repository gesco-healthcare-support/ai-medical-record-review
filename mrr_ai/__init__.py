"""MRR AI application package (Flask application factory)."""

from flask import Flask

from mrr_ai.config import MAX_CONTENT_LENGTH, UPLOAD_FOLDER


def create_app():
    """Build and configure the Flask app, registering all blueprints."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

    from mrr_ai.blueprints import register_blueprints

    register_blueprints(app)
    return app
