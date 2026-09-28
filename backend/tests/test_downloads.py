"""#389: an export is prepared on the server and fetched by the browser as a native download.

Every export used to answer its POST with the file itself, which the page read into a Blob. Chrome cannot page
a large Blob to disk on a machine short of space, and on the shared reviewer host it cut three of four exports
short on 2026-09-24 (15.8 of 19.5 MB, 16.8 of 22.2, 16.2 of 37.0). Now the POST parks the finished file behind a
random, short-lived token and answers with where to fetch it; the browser's own download manager then streams
it to disk. These tests pin that contract: what the POST returns, how the GET sends the file, who may fetch it,
for how long, and that the file - patient data at rest - is gone once the token expires.

Synthetic data only: blank PDFs, invented names.
"""

import io
import logging
import os
import time
from pathlib import Path

import anyio
import pytest
from pypdf import PdfWriter
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from app.api.downloads import _MeasuredFileResponse
from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AuditLog, Document, User
from app.services import downloads
from tests.conftest import unique_test_email

# A name that must never reach a log line: the memo's filename carries it (see `_name_the_patient`).
_PATIENT_LAST = "Synthetic-Lastname"


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    # Uploads AND prepared downloads land under a per-test tmp dir (pytest cleans it up).
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture
async def authed(client, seeded_user):
    """The shared client, logged in as the seeded user; yields (client, user_id)."""
    email, password = seeded_user
    resp = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 204
    with get_sessionmaker()() as session:
        user_id = session.scalar(select(User.id).where(User.email == email))
    return client, user_id


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def _upload(client) -> str:
    resp = await client.post(
        "/api/documents", files={"pdf": ("scan.pdf", _blank_pdf(), "application/pdf")}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _name_the_patient(doc_id: str) -> None:
    with get_sessionmaker()() as session:
        document = session.get(Document, doc_id)
        document.patient_first_name = "Synthetic"
        document.patient_last_name = _PATIENT_LAST
        session.commit()


async def _prepare(client, doc_id: str) -> dict:
    """POST the memo export - it needs no summaries - and return what it answered."""
    resp = await client.post(f"/api/documents/{doc_id}/export/memo", json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _download_audits(doc_id: str) -> list[AuditLog]:
    with get_sessionmaker()() as session:
        return list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.document_id == doc_id, AuditLog.action == "download"
                )
            ).all()
        )


# --- the POST: a download address, not the file ------------------------------------------------


async def test_an_export_answers_with_where_to_download_it_not_the_file(authed):
    """WHEN an export is prepared, THE SYSTEM SHALL answer {token, filename, size, url} as JSON."""
    client, user_id = authed
    doc_id = await _upload(client)

    resp = await client.post(f"/api/documents/{doc_id}/export/memo", json={})

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    prepared = resp.json()
    assert set(prepared) == {"token", "filename", "size", "url"}
    assert prepared["url"] == f"/api/documents/{doc_id}/downloads/{prepared['token']}"
    assert prepared["filename"].endswith(".docx")
    # The file waits in the user's own folder, under the token - never under the patient's name.
    parked = os.path.join(
        get_settings().upload_folder, str(user_id), "downloads", prepared["token"]
    )
    assert os.path.getsize(parked) == prepared["size"]


# --- the GET: the file, whole, to its owner, while the token lives -----------------------------


async def test_the_download_is_the_file_sent_whole_under_its_name(authed):
    """WHEN the owner GETs a live token, THE SYSTEM SHALL send the whole file, named, uncached."""
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)

    got = await client.get(prepared["url"])

    assert got.status_code == 200, got.text
    # Sent whole with its length declared, so the browser can tell a cut-off transfer from a whole one.
    assert got.headers["content-length"] == str(prepared["size"]) == str(len(got.content))
    assert got.headers["content-disposition"] == f'attachment; filename="{prepared["filename"]}"'
    assert got.headers["cache-control"] == "no-store"
    assert got.content[:2] == b"PK"  # a .docx is a zip


async def test_the_owner_can_fetch_it_again_before_it_expires(authed):
    """WHEN the owner GETs a token again before it expires, THE SYSTEM SHALL send it again."""
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)

    first = await client.get(prepared["url"])
    second = await client.get(prepared["url"])

    assert first.status_code == second.status_code == 200
    assert first.content == second.content


async def test_a_range_request_gets_just_that_range(authed):
    """WHEN a download carries a Range header, THE SYSTEM SHALL answer 206 with that range.

    It is what Chrome's own Resume sends for an interrupted download."""
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)

    part = await client.get(prepared["url"], headers={"Range": "bytes=0-9"})

    assert part.status_code == 206, part.text
    assert len(part.content) == 10


async def test_each_download_writes_one_audit_row_naming_no_file(authed):
    """WHEN a download is fetched, THE SYSTEM SHALL write one 'download' audit row, ids only."""
    client, user_id = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)

    await client.get(prepared["url"])
    await client.get(prepared["url"])

    rows = _download_audits(doc_id)
    assert len(rows) == 2, "one 'download' audit row per GET"
    assert all(row.user_id == user_id and row.detail is None for row in rows)


# --- refusals: always 404, the same answer as a document that does not exist -------------------


async def test_another_users_download_is_404(authed):
    """WHEN someone else requests a download, THE SYSTEM SHALL answer 404 - never 403."""
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)

    email_b, password_b = unique_test_email(), "Str0ng#pw1"
    with get_sessionmaker()() as session:
        session.add(
            User(
                email=email_b, name="B", password=MrrPasswordHelper().hash(password_b), active=True
            )
        )
        session.commit()
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", data={"username": email_b, "password": password_b})

    assert (await client.get(prepared["url"])).status_code == 404


async def test_a_token_is_refused_under_another_record(authed):
    """WHEN a token is requested under a record it was not made for, THE SYSTEM SHALL answer 404."""
    client, _ = authed
    doc_a = await _upload(client)
    doc_b = await _upload(client)
    prepared = await _prepare(client, doc_a)

    resp = await client.get(f"/api/documents/{doc_b}/downloads/{prepared['token']}")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}


@pytest.mark.parametrize("token", ["not-a-token", "A" * 43])
async def test_a_malformed_or_unknown_token_is_404(authed, token):
    """WHEN the token is malformed, or well-formed but unknown, THE SYSTEM SHALL answer 404."""
    client, _ = authed
    doc_id = await _upload(client)

    resp = await client.get(f"/api/documents/{doc_id}/downloads/{token}")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}


async def test_an_expired_token_is_404(authed, monkeypatch):
    """WHEN the token has expired, THE SYSTEM SHALL answer 404."""
    monkeypatch.setattr(get_settings(), "download_ttl_seconds", 1)
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)

    time.sleep(1.5)

    assert (await client.get(prepared["url"])).status_code == 404


def test_lookup_refuses_a_token_for_another_user():
    """WHEN a token is looked up for a user who did not prepare it, THE SYSTEM SHALL find nothing.

    Tested on the service, not the route: through the route, `get_owned_document` already refuses a
    record the requester does not own, so a route test could not tell whether this check exists.

    THE DECOY IS LOAD-BEARING. `lookup` also looks for the file in the REQUESTER's own folder, so with
    nothing under user 2 that path check alone refuses them - and this test passed with the metadata's
    user check deleted (probe P1, 2026-09-24). A same-named file in user 2's folder leaves the metadata
    check as the only thing that can say no."""
    prepared = downloads.prepare(
        user_id=1, document_id="doc-1", content=b"x" * 10, media_type="text/plain", filename="f.txt"
    )
    decoy = os.path.join(get_settings().upload_folder, "2", "downloads", prepared["token"])
    os.makedirs(os.path.dirname(decoy))
    with open(decoy, "wb") as fh:
        fh.write(b"decoy")

    assert downloads.lookup(prepared["token"], user_id=2, document_id="doc-1") is None
    assert downloads.lookup(prepared["token"], user_id=1, document_id="doc-1") is not None


# --- the file leaves the disk -----------------------------------------------------------------


def test_the_file_is_deleted_when_its_token_expires(monkeypatch):
    """WHEN download_ttl_seconds passes, THE SYSTEM SHALL delete the prepared file."""
    monkeypatch.setattr(get_settings(), "download_ttl_seconds", 1)
    prepared = downloads.prepare(
        user_id=1, document_id="doc-1", content=b"x" * 10, media_type="text/plain", filename="f.txt"
    )
    parked = os.path.join(get_settings().upload_folder, "1", "downloads", prepared["token"])
    assert os.path.exists(parked)

    time.sleep(2)

    assert not os.path.exists(parked), "the expiry timer did not delete the file"


def test_the_redis_entry_expires_with_the_token():
    """WHEN a download is prepared, THE SYSTEM SHALL give its Redis entry the download expiry."""
    from app.worker.queues import get_redis

    prepared = downloads.prepare(
        user_id=1, document_id="doc-1", content=b"x", media_type="text/plain", filename="f.txt"
    )

    remaining = get_redis().ttl(f"download:{prepared['token']}")
    assert 0 < remaining <= get_settings().download_ttl_seconds


def test_the_sweep_removes_stale_files_and_keeps_fresh_ones():
    """WHEN a sweep runs, THE SYSTEM SHALL delete download files older than the expiry - finished or
    half-written - and keep the rest."""
    folder = os.path.join(get_settings().upload_folder, "7", "downloads")
    os.makedirs(folder)
    stale = os.path.join(folder, "stale")
    half = os.path.join(folder, "stale.part")
    fresh = os.path.join(folder, "fresh")
    for path in (stale, half, fresh):
        with open(path, "wb") as fh:
            fh.write(b"x")
    old = time.time() - get_settings().download_ttl_seconds - 10
    os.utime(stale, (old, old))
    os.utime(half, (old, old))

    assert downloads.sweep() == 2
    assert os.path.exists(fresh)
    assert not os.path.exists(stale)
    assert not os.path.exists(half)


def test_preparing_a_download_sweeps_first():
    """WHEN a download is prepared, THE SYSTEM SHALL first sweep away anything already expired."""
    folder = os.path.join(get_settings().upload_folder, "7", "downloads")
    os.makedirs(folder)
    stale = os.path.join(folder, "left-behind")
    with open(stale, "wb") as fh:
        fh.write(b"x")
    old = time.time() - get_settings().download_ttl_seconds - 10
    os.utime(stale, (old, old))

    downloads.prepare(
        user_id=1, document_id="doc-1", content=b"x", media_type="text/plain", filename="f.txt"
    )

    assert not os.path.exists(stale)


def test_the_sweep_skips_a_file_it_cannot_delete_and_removes_the_rest(monkeypatch, caplog):
    """IF a stale download cannot be deleted, THEN THE SYSTEM SHALL skip it, still delete the others, and log
    one warning giving the count and error type only (decided 2026-09-24, #389).

    The stuck file is named the way a real one is - by its token - and a token is what grants the download,
    so the warning must not carry it."""
    folder = os.path.join(get_settings().upload_folder, "7", "downloads")
    os.makedirs(folder)
    stuck_token = "S" * 43
    stuck = os.path.join(folder, stuck_token)
    other = os.path.join(folder, "other-stale")
    old = time.time() - get_settings().download_ttl_seconds - 10
    for path in (stuck, other):
        with open(path, "wb") as fh:
            fh.write(b"x")
        os.utime(path, (old, old))
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == stuck_token:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    with caplog.at_level(logging.WARNING, logger="app.services.downloads"):
        assert downloads.sweep() == 1

    assert os.path.exists(stuck)
    assert not os.path.exists(other), "one stuck file stopped the rest being swept"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, warnings
    assert "could not remove 1 stale file(s)" in warnings[0]
    assert "PermissionError" in warnings[0]
    assert stuck_token not in warnings[0]


async def test_an_export_goes_ahead_when_the_sweep_before_it_fails(authed, monkeypatch, caplog):
    """IF the sweep before an export fails outright, THEN THE SYSTEM SHALL still prepare the export and log
    a warning naming only the error type (decided 2026-09-24, #389)."""
    client, _ = authed
    doc_id = await _upload(client)

    def sweep_fails():
        raise PermissionError(13, "Permission denied", "S" * 43)

    monkeypatch.setattr(downloads, "sweep", sweep_fails)

    with caplog.at_level(logging.WARNING, logger="app.services.downloads"):
        prepared = await _prepare(client, doc_id)

    assert (await client.get(prepared["url"])).status_code == 200
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("PermissionError" in message for message in warnings), warnings
    assert not any("S" * 43 in message for message in warnings)


# --- Redis unavailable, and what the logs may hold ----------------------------------------------


async def test_an_export_answers_503_and_leaves_nothing_when_redis_is_down(authed, monkeypatch):
    """IF Redis is unreachable when an export is prepared, THEN THE SYSTEM SHALL answer 503 and leave
    no file behind."""
    client, user_id = authed
    doc_id = await _upload(client)

    class _DownRedis:
        def set(self, *args, **kwargs):
            raise RedisConnectionError("redis is unreachable")

    monkeypatch.setattr(downloads, "get_redis", lambda: _DownRedis())

    resp = await client.post(f"/api/documents/{doc_id}/export/memo", json={})

    assert resp.status_code == 503, resp.text
    folder = os.path.join(get_settings().upload_folder, str(user_id), "downloads")
    assert not os.path.isdir(folder) or os.listdir(folder) == []


async def test_a_download_answers_503_when_redis_is_down_at_fetch_time(authed, monkeypatch):
    """IF Redis is unreachable when a download is fetched, THEN THE SYSTEM SHALL answer 503 with the plain
    message - not a 404 that tells the reviewer the file is gone when it is not."""
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)

    class _DownRedis:
        def get(self, *args, **kwargs):
            raise RedisConnectionError("redis is unreachable")

    monkeypatch.setattr(downloads, "get_redis", lambda: _DownRedis())

    resp = await client.get(prepared["url"])

    assert resp.status_code == 503
    assert resp.json() == {"detail": "Downloads are unavailable right now. Please try again."}


async def test_a_live_token_whose_file_is_gone_is_404(authed):
    """WHEN a token is still in Redis but its file is already gone (swept, or deleted by its timer), THE
    SYSTEM SHALL answer 404 rather than fail to send it."""
    client, user_id = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)
    os.remove(
        os.path.join(get_settings().upload_folder, str(user_id), "downloads", prepared["token"])
    )

    resp = await client.get(prepared["url"])

    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}


async def test_no_log_line_carries_the_filename(authed, caplog):
    """The filename carries the patient's name, so no log line may contain it."""
    client, _ = authed
    doc_id = await _upload(client)
    _name_the_patient(doc_id)

    with caplog.at_level(logging.DEBUG):
        prepared = await _prepare(client, doc_id)
        await client.get(prepared["url"])

    assert _PATIENT_LAST in prepared["filename"], "the test needs a filename carrying the name"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert _PATIENT_LAST not in logged


# --- #390: every download's outcome is measured and logged ----------------------------------------

_TOKEN = (
    "T" * 8 + "u" * 35
)  # the shape `secrets.token_urlsafe(32)` gives; only the first 8 may be logged


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    """A minimal ASGI http scope for driving a response directly, as uvicorn would."""
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers or [],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
        "extensions": {},
    }


def _measured(path) -> _MeasuredFileResponse:
    return _MeasuredFileResponse(
        str(path), media_type="application/octet-stream", document_id="doc-1", token=_TOKEN
    )


async def test_a_full_transfer_is_logged_as_complete(tmp_path, caplog):
    """WHEN a download GET sends the whole file, THE SYSTEM SHALL log it as complete at INFO, with the bytes
    sent, the expected size and the time taken."""
    path = tmp_path / "file"
    path.write_bytes(b"x" * (3 * 65536 + 10))
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        await anyio.sleep_forever()  # the browser never leaves

    with caplog.at_level(logging.INFO, logger="app.api.downloads"):
        await _measured(path)(_scope(), receive, send)

    lines = [r for r in caplog.records if r.name == "app.api.downloads"]
    assert [r.levelno for r in lines] == [logging.INFO]
    message = lines[0].getMessage()
    assert message.startswith("download complete: document=doc-1 token=TTTTTTTT status=200 ")
    assert f"sent={3 * 65536 + 10} of {3 * 65536 + 10} bytes in " in message


async def test_a_browser_that_leaves_early_is_logged_as_interrupted_and_stops_the_send(
    tmp_path, caplog
):
    """IF the browser disconnects before the last byte, THEN THE SYSTEM SHALL stop reading the file and log
    the download as interrupted at WARNING, with how much was sent.

    Without the watcher, uvicorn's `send` returns silently after a disconnect (#390 research), so the loop
    would read the whole file into nothing and the log would say it completed."""
    chunks = 10
    path = tmp_path / "file"
    path.write_bytes(b"x" * (chunks * 65536 + 10))
    two_chunks_out = anyio.Event()
    bodies = 0

    async def send(message):
        nonlocal bodies
        if message["type"] == "http.response.body":
            bodies += 1
            if bodies == 2:
                two_chunks_out.set()
        await anyio.sleep(0)  # a real socket write is a checkpoint too

    async def receive():
        await two_chunks_out.wait()
        return {"type": "http.disconnect"}

    with caplog.at_level(logging.INFO, logger="app.api.downloads"):
        await _measured(path)(_scope(), receive, send)

    assert bodies < chunks, "the send loop kept reading the file after the browser left"
    lines = [r for r in caplog.records if r.name == "app.api.downloads"]
    assert [r.levelno for r in lines] == [logging.WARNING]
    message = lines[0].getMessage()
    assert message.startswith("download interrupted: document=doc-1 token=TTTTTTTT status=200 ")
    assert message.endswith(", client closed the connection")
    assert f"of {chunks * 65536 + 10} bytes" in message


async def test_a_range_request_is_measured_against_the_range(tmp_path, caplog):
    """WHEN a download GET carries a Range header, THE SYSTEM SHALL measure it against that range (206)."""
    path = tmp_path / "file"
    path.write_bytes(b"x" * 1000)

    async def send(message):
        pass

    async def receive():
        await anyio.sleep_forever()

    with caplog.at_level(logging.INFO, logger="app.api.downloads"):
        await _measured(path)(_scope([(b"range", b"bytes=0-99")]), receive, send)

    message = next(r.getMessage() for r in caplog.records if r.name == "app.api.downloads")
    assert " status=206 sent=100 of 100 bytes in " in message


async def test_a_download_turns_off_proxy_buffering_and_logs_ids_only(authed, caplog):
    """WHEN the owner fetches a download, THE SYSTEM SHALL tell the proxy not to buffer it, and log it by
    record id and the token's first 8 characters - never the whole token, never the filename.

    `X-Accel-Buffering: no` is what lets the API see the browser at all: with nginx buffering (its default)
    nginx takes the whole file at once and the API's count says nothing about the reviewer's download."""
    client, _ = authed
    doc_id = await _upload(client)
    _name_the_patient(doc_id)
    prepared = await _prepare(client, doc_id)

    with caplog.at_level(logging.INFO, logger="app.api.downloads"):
        got = await client.get(prepared["url"])

    assert got.headers["x-accel-buffering"] == "no"
    lines = [r.getMessage() for r in caplog.records if r.name == "app.api.downloads"]
    assert len(lines) == 1, lines
    assert lines[0].startswith(
        f"download complete: document={doc_id} token={prepared['token'][:8]} status=200 "
    )
    assert prepared["token"] not in lines[0]
    assert _PATIENT_LAST not in lines[0]


async def test_a_get_that_raises_is_logged_as_failed_and_still_raises(tmp_path, caplog):
    """IF a download GET raises part-way, THEN THE SYSTEM SHALL log it as failed at WARNING, naming only the
    error's type, and let the error through (#390 amendment A1, 2026-09-25).

    Before this, such a GET wrote no download line at all. The error's own text is never logged: it can
    carry a path, and a path carries the token - which this test's error deliberately does."""
    path = tmp_path / "file"
    path.write_bytes(b"x" * (3 * 65536))
    bodies = 0

    async def send(message):
        nonlocal bodies
        if message["type"] == "http.response.body":
            bodies += 1
            if bodies == 2:
                raise OSError(5, "I/O error", f"/uploads/1/downloads/{_TOKEN}")

    async def receive():
        await anyio.sleep_forever()

    response, scope = _measured(path), _scope()
    with caplog.at_level(logging.INFO, logger="app.api.downloads"):
        # The anyio task group delivers the error inside an ExceptionGroup (seen 2026-09-25).
        with pytest.raises(ExceptionGroup) as caught:
            await response(scope, receive, send)

    assert caught.group_contains(OSError), "the error was swallowed or changed"
    lines = [r for r in caplog.records if r.name == "app.api.downloads"]
    assert [r.levelno for r in lines] == [logging.WARNING], "a failed GET must not read as complete"
    message = lines[0].getMessage()
    assert message.startswith("download failed: document=doc-1 token=TTTTTTTT status=200 ")
    assert message.endswith("(OSError)")
    assert _TOKEN not in message
