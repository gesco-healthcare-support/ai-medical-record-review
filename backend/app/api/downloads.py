"""The GET half of an export (#389): send a prepared file to its owner as a native download.

An export's POST builds the file and parks it (`app/services/downloads.py`); the browser then fetches it here
with an ordinary link, so its own download manager streams the body to disk instead of the page holding it in
a Blob. Under `/api/documents/<id>/...` so `get_owned_document` refuses a record the requester does not own
before the token is even read; the token must then be one this user made for this record. Every refusal is
the same 404 as a missing document, so a request cannot confirm that a token or a record exists.

Sent by `FileResponse`: the body goes out whole with its `Content-Length` declared - the browser can tell a
cut-off transfer from a complete one - and a `Range` request (Chrome's Resume) gets a 206. The streaming the
old POST responses used went out in fragments: a 50.2 MB linked PDF took 260,433 of them and 25.6 s,
measured on the server, and a tester's side cut every large export off near 10.78 MB.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_owned_document
from app.auth.deps import current_active_user
from app.db import get_db
from app.models import Document, User
from app.services import downloads
from app.services.audit import audit

router = APIRouter(prefix="/api/documents", tags=["downloads"])

DOWNLOADS_UNAVAILABLE = "Downloads are unavailable right now. Please try again."


@router.get(
    "/{document_id}/downloads/{token}",
    responses={
        404: {"description": "No such download for this user and record, or it has expired."},
        503: {"description": "The download store is unavailable."},
    },
)
def download_file(
    token: str,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """The prepared file for `token`, as an attachment. One `download` audit row per request."""
    try:
        prepared = downloads.lookup(token, user_id=user.id, document_id=document.id)
    except downloads.DownloadsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=DOWNLOADS_UNAVAILABLE
        ) from exc
    if prepared is None:
        # The same body `get_owned_document` answers with, so a refusal says nothing about why.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    audit(session, "download", user.id, document.id)
    return FileResponse(
        prepared.path,
        media_type=prepared.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{prepared.filename}"',
            # Patient data: no browser or proxy cache may keep a copy.
            "Cache-Control": "no-store",
        },
    )
