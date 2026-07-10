"""Audit trail: who did what to which document, when.

Rows reference ids only - the original filename is PHI and must never reach the audit
table or any log line (log/audit by document id; the owner sees the filename in the UI).
"""

from mrr_ai.extensions import db
from mrr_ai.models import AuditLog


def audit(action, document_id=None, user_id=None):
    """Record ``action`` for the current (or given) user; commits its own row."""
    if user_id is None:
        from flask_security import current_user

        user_id = current_user.id
    db.session.add(AuditLog(user_id=user_id, action=action, document_id=document_id))
    db.session.commit()
