"""Shared dependencies for the domain routers.

`get_owned_document` is the IDOR guard: it resolves the document by id AND the authenticated
user, returning 404 (never 403) on a miss - so a non-owner cannot even confirm a document exists.
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
    """The current user's document by id, or 404 (the user_id check IS the IDOR guard)."""
    document = session.get(Document, document_id)
    if document is None or document.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return document
