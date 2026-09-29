"""Shared dependencies for the domain routers.

`get_owned_document` is the IDOR guard: it resolves the document by id AND the authenticated
user, returning 404 (never 403) on a miss - so a non-owner cannot even confirm a document exists.
An ADMIN passes it for any document: the client's lead reviewer asked to open and fix the records
other reviewers are working on "if there is a mistake but they already left". Everyone else is held
to ownership exactly as before. Whoever acts is recorded - every audit row takes the acting user,
not the owner - and deleting a record stays owner-only (`delete_document`).
The domain layer runs on the SYNC session (get_db); the current-user dependency comes from the
async FastAPI-Users backend (only the user id is read across the two).
"""

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import current_active_user
from app.db import get_db
from app.models import Document, User


def get_owned_document(
    document_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> Document:
    """The document by id if the current user owns it or is an admin, else 404 (the user_id check
    IS the IDOR guard)."""
    document = session.get(Document, document_id)
    if document is None or not (document.user_id == user.id or user.is_superuser):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return document
