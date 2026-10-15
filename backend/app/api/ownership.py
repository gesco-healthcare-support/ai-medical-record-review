"""Whose account a record lands in when an admin acts for another reviewer.

The client's lead reviewer asked for two things beyond opening another reviewer's records (which
`get_owned_document` already allows an admin): to UPLOAD a record into another reviewer's account,
and to ASSIGN a record to someone to work on. Both put a record in an account other than the
caller's, so both answer the same two questions, and they are answered once here so the upload
route and the assign route cannot disagree:

* which account it goes to - an active reviewer, or 404 (`reviewer_for`, `upload_owner`);
* whether that account already holds the same file (`same_file`), in which case the admin is told
  and asked to confirm before a second copy lands there (`duplicate_conflict`).

A reviewer uploading into their OWN account keeps the old behaviour: a duplicate is a warning in the
response, never a refusal. The confirmation exists for the cross-account case because the admin
cannot see at a glance what the other reviewer already has, and a second copy in someone else's list
is work they may do twice.
"""

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, User

# The `code` a duplicate refusal carries, so the client can tell it apart from every other 409 (a
# job running, say) and offer "upload anyway" for this one only.
DUPLICATE_CODE = "duplicate"

_NO_SUCH_REVIEWER = "reviewer not found"
_ADMIN_ONLY_UPLOAD = "only an admin can upload into another reviewer's records"


def reviewer_for(session: Session, owner_id: int) -> User:
    """The ACTIVE account `owner_id`, or 404. A switched-off account cannot be working on a record,
    which is also why `/api/admin/users` leaves it out of the list an admin picks from."""
    reviewer = session.get(User, owner_id)
    if reviewer is None or not reviewer.active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SUCH_REVIEWER)
    return reviewer


def upload_owner(session: Session, user: User, owner: int | None) -> User:
    """The account an upload lands in: the caller's own unless an admin named someone else.

    REFUSED for anyone else who names another account, unlike the records list, which ignores a
    non-admin's `owner`. Ignoring it there costs nothing (they see their own list); ignoring it here
    would put the file somewhere other than where the caller asked, so the write is refused instead.
    The answer is the same whether or not that account exists, so it confirms nothing about it.
    """
    if owner is None or owner == user.id:
        return user
    if not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_ADMIN_ONLY_UPLOAD)
    return reviewer_for(session, owner)


def same_file(
    session: Session, owner_id: int, sha256: str, *, exclude: str | None = None
) -> list[Document]:
    """The records `owner_id` already holds with this exact file, newest first. `exclude` leaves
    out the record being moved, which on an assignment is the one file guaranteed to match."""
    query = select(Document).where(Document.user_id == owner_id, Document.sha256 == sha256)
    if exclude is not None:
        query = query.where(Document.id != exclude)
    return list(session.scalars(query.order_by(Document.created_at.desc())).all())


def duplicate_conflict(reviewer: User, copies: list[Document]) -> JSONResponse:
    """409 saying the reviewer already holds this file, for the admin to confirm or drop.

    The sentence is shown to the admin as it is, so it carries the reviewer's name, the date and the
    page count - enough to recognise the record - and never the file name, which can name a patient.
    """
    who = reviewer.name or "This reviewer"
    latest = copies[0]
    when = latest.created_at.strftime("%m/%d/%Y")
    if len(copies) == 1:
        detail = f"{who} already has this record (uploaded {when}, {latest.page_count} pages)."
    else:
        detail = (
            f"{who} already has {len(copies)} copies of this record (the latest uploaded {when}, "
            f"{latest.page_count} pages)."
        )
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": detail,
            "code": DUPLICATE_CODE,
            "duplicates": [{"id": copy.id, "page_count": copy.page_count} for copy in copies],
        },
    )
