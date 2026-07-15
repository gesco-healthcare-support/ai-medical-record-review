"""Ported AI + document services (Flask-free).

Copied from the Flask `mrr_ai/services` layer, rewired to the FastAPI backend: `mrr_ai.config`
-> `app.config.get_settings()`, `mrr_ai.extensions.db.session` / `Model.query` -> an explicit
SQLAlchemy `Session` argument, and the built genai client -> a lazy `genai_client.get_genai_client`.
The classifier (torch) services (segment_engine, classification, verify_pass, windows) are NOT
ported here - they belong to the P4 worker tier.
"""
