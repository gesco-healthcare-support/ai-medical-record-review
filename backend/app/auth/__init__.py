"""Authentication package (P2): FastAPI-Users wiring reproducing the Flask-Security scheme.

Modules:
- password: the Flask-Security-compatible PasswordHelper (HMAC-SHA512 -> argon2id).
- db: the SQLAlchemy user + access-token database adapters.
- backend: the cookie transport + database session strategy + authentication backend.
"""
