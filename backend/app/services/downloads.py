"""Prepared downloads (#389): an export is built by its POST, parked here, and fetched by a GET.

Every export used to answer its POST with the file itself, which the page read into a Blob before saving it.
Chrome cannot page a large Blob to disk on a machine short of space, and cancels the body part-way: on the
shared reviewer host it cut three of four exports short on 2026-09-24 (15.8 of 19.5 MB, 16.8 of 22.2 and
16.2 of 37.0). So the POST now parks the finished file here behind a random token and answers with where to
fetch it, and the browser's own download manager streams it to disk.

THE FILE IS PATIENT DATA AT REST. It lives in the requesting user's own upload folder,
`<upload_folder>/<user_id>/downloads/<token>` - named by the token, never by the patient - for at most
`download_ttl_seconds`. Three things delete it: a timer set when it is written, a sweep before every new
export, and a sweep at API startup (for timers a restart cancelled). A file a sweep cannot delete is skipped,
reported by count and error type, and tried again by the next sweep. Its metadata - including the filename,
which carries the patient's name - sits in Redis under `download:<token>` for the same span; this Redis runs
without persistence, so that never reaches its disk. Nothing here logs a filename: counts only.

The owner may fetch the file more than once until it expires, so Chrome's own Resume (a Range request on the
same URL) and a second click both work.
"""

import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from redis.exceptions import RedisError

from app.config import get_settings
from app.worker.queues import get_redis

logger = logging.getLogger(__name__)

# `secrets.token_urlsafe(32)` is always 43 characters from this alphabet. Anything else is refused before it
# goes anywhere near a path or a Redis key.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


class DownloadsUnavailable(Exception):
    """The download store (Redis) could not be reached, so nothing was prepared or found."""


@dataclass(frozen=True)
class Prepared:
    """A download that is ready to be sent: where it is, and how to name and label it."""

    path: str
    media_type: str
    filename: str
    size: int


def _key(token: str) -> str:
    return f"download:{token}"


def _folder(user_id: int) -> Path:
    return Path(get_settings().upload_folder) / str(user_id) / "downloads"


def _delete(path: Path) -> None:
    """Remove a prepared file; one already gone is not an error."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def prepare(
    *, user_id: int, document_id: str, content: bytes, media_type: str, filename: str
) -> dict:
    """Park `content` for `user_id` and return `{token, filename, size, url}` for the browser.

    Written to a `.part` file and renamed, so a half-written file is never served. Raises
    `DownloadsUnavailable` - having removed the file - when Redis cannot record it."""
    try:
        sweep()
    except OSError as exc:
        # Decided 2026-09-24 (#389): tidying OLD files never blocks a new export. Failing it would not delete
        # the stuck file either. The error type only - its message carries a path, and a path carries a token.
        logger.warning("download sweep before an export failed (%s)", type(exc).__name__)
    ttl = get_settings().download_ttl_seconds
    folder = _folder(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path = folder / token
    part = folder / f"{token}.part"
    part.write_bytes(content)
    os.replace(part, path)
    meta = {
        "user_id": user_id,
        "document_id": document_id,
        "media_type": media_type,
        "filename": filename,
        "size": len(content),
    }
    try:
        get_redis().set(_key(token), json.dumps(meta), ex=ttl)
    except RedisError as exc:
        _delete(path)
        raise DownloadsUnavailable from exc
    # Deletes the file the moment the token expires, instead of whenever the next sweep happens to run.
    timer = threading.Timer(ttl, _delete, args=(path,))
    timer.daemon = True
    timer.start()
    return {
        "token": token,
        "filename": filename,
        "size": len(content),
        "url": f"/api/documents/{document_id}/downloads/{token}",
    }


def lookup(token: str, *, user_id: int, document_id: str) -> Prepared | None:
    """The prepared download for `token`, or None unless it is well-formed, unexpired, and was made by
    `user_id` for `document_id`. Raises `DownloadsUnavailable` when Redis cannot be reached."""
    if not _TOKEN.fullmatch(token):
        return None
    try:
        raw = get_redis().get(_key(token))
    except RedisError as exc:
        raise DownloadsUnavailable from exc
    if raw is None:
        return None
    meta = json.loads(raw)
    if meta["user_id"] != user_id or meta["document_id"] != document_id:
        return None
    path = _folder(user_id) / token
    if not path.is_file():
        return None
    return Prepared(str(path), meta["media_type"], meta["filename"], meta["size"])


def sweep(now: float | None = None) -> int:
    """Delete every prepared file - finished or half-written - older than the expiry; return how many.

    A file that cannot be deleted is skipped and the rest are still swept. One warning says how many were
    stuck and why, never which: each is named by its token, and a token grants the download."""
    now = time.time() if now is None else now
    ttl = get_settings().download_ttl_seconds
    removed = 0
    stuck: list[str] = []
    for entry in Path(get_settings().upload_folder).glob("*/downloads/*"):
        try:
            if now - entry.stat().st_mtime > ttl:
                entry.unlink()
                removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            stuck.append(type(exc).__name__)
    if stuck:
        logger.warning(
            "download sweep could not remove %d stale file(s) (%s)",
            len(stuck),
            ", ".join(sorted(set(stuck))),
        )
    return removed
