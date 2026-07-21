"""Audit trail: who did what to which document, when.

Rows reference ids only - the original filename is PHI and must never reach the audit table or
any log line. The Flask version defaulted user_id from flask_security.current_user; the FastAPI
port takes an explicit session + user_id (the router passes the authenticated user's id).
"""

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(session: Session, action: str, user_id: int, document_id: str | None = None) -> None:
    """Record ``action`` for ``user_id``; commits its own row on the given session."""
    session.add(AuditLog(user_id=user_id, action=action, document_id=document_id))
    session.commit()
