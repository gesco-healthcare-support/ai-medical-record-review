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

Every GET is MEASURED (#390): once the page hands the link to the browser, a failed download shows only in
Chrome's download bar, so this is the one place that can record it. One log line per GET - record id, the
token's first 8 characters, bytes sent against the expected size, time taken, and whether the browser left
before the end. Never the filename (it carries the patient's name) and never the whole token.
"""

import logging
import time

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.types import Message, Receive, Scope, Send

from app.api.deps import get_owned_document
from app.auth.deps import current_active_user
from app.db import get_db
from app.models import Document, User
from app.services import downloads
from app.services.audit import audit

router = APIRouter(prefix="/api/documents", tags=["downloads"])
logger = logging.getLogger(__name__)

DOWNLOADS_UNAVAILABLE = "Downloads are unavailable right now. Please try again."


class _MeasuredFileResponse(FileResponse):
    """A `FileResponse` that logs what it delivered, and stops sending when the browser leaves (#390).

    Two facts about the stack make this necessary (read from the installed source, 2026-09-25): uvicorn's
    `send` returns SILENTLY once the client has gone, and starlette's `FileResponse` never listens for the
    disconnect - so a plain one reads the whole file into nothing and cannot tell a cut download from a whole
    one. The disconnect arrives on `receive()`, which this watches in parallel, the way starlette's own
    `StreamingResponse` does. `receive()` also reports a disconnect once the response is complete, so only one
    that comes before the last chunk counts as the browser leaving."""

    def __init__(self, path: str, *, document_id: str, token: str, **kwargs) -> None:
        super().__init__(path, **kwargs)
        self._document_id = document_id
        self._token8 = token[:8]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        progress = {"status": None, "expected": None, "sent": 0, "finished": False}
        left_early = False

        async def counting_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                progress["status"] = message["status"]
                for name, value in message.get("headers", []):
                    if name.lower() == b"content-length":
                        progress["expected"] = int(value)
            elif message["type"] == "http.response.body":
                progress["sent"] += len(message.get("body", b""))
                # Set BEFORE the send: the last chunk's send is what makes `receive()` report completion.
                progress["finished"] = not message.get("more_body", False)
            await send(message)

        started = time.monotonic()
        async with anyio.create_task_group() as group:

            async def watch_for_disconnect() -> None:
                nonlocal left_early
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        if not progress["finished"]:
                            left_early = True
                            group.cancel_scope.cancel()
                        return

            group.start_soon(watch_for_disconnect)
            await super().__call__(scope, receive, counting_send)
            group.cancel_scope.cancel()
        self._log(progress, left_early, time.monotonic() - started)

    def _log(self, progress: dict, left_early: bool, seconds: float) -> None:
        expected = progress["expected"] if progress["expected"] is not None else "?"
        fields = (
            self._document_id,
            self._token8,
            progress["status"],
            progress["sent"],
            expected,
            seconds,
        )
        if left_early:
            logger.warning(
                "download interrupted: document=%s token=%s status=%s sent=%d of %s bytes in %.1fs, "
                "client closed the connection",
                *fields,
            )
        else:
            logger.info(
                "download complete: document=%s token=%s status=%s sent=%d of %s bytes in %.1fs",
                *fields,
            )


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
    return _MeasuredFileResponse(
        prepared.path,
        document_id=document.id,
        token=token,
        media_type=prepared.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{prepared.filename}"',
            # Patient data: no browser or proxy cache may keep a copy.
            "Cache-Control": "no-store",
            # nginx buffers by default: it takes the whole file at once (spilling to its own temp files), so
            # the API finishes long before the browser and never sees it leave. Off, nginx passes bytes at
            # the browser's pace and closes this connection when the browser goes - what the log measures.
            "X-Accel-Buffering": "no",
        },
    )
