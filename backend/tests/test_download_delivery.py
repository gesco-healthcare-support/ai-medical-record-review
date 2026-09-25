"""#390 PR 2: what happened to each download, as the page can ask for it.

After an export is handed to the browser, the page cannot see the download at all. The server's measured GET
(PR 1) now also keeps a small delivery record per download - ids and byte offsets only, 15 minutes, in Redis's
memory - and a status endpoint answers one of: waiting, downloading, interrupted, complete, expired.

A Chrome Resume is a `Range` request for the rest of the file. It completes the download only if it starts at
or before what was already delivered - so the record tracks the longest delivered prefix, not a byte total.

Synthetic data only.
"""

import logging
import os

import anyio
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from app.api.downloads import _MeasuredFileResponse
from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import User
from app.services import downloads
from app.worker.queues import get_redis
from tests.conftest import unique_test_email
from tests.test_downloads import _prepare, _upload

SIZE = 1000


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    # Uploads and prepared downloads land under a per-test tmp dir (pytest cleans it up).
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


def _park(size: int = SIZE) -> dict:
    """A prepared download for user 1 on record doc-1, straight through the service."""
    return downloads.prepare(
        user_id=1,
        document_id="doc-1",
        content=b"x" * size,
        media_type="text/plain",
        filename="f.txt",
    )


def _state(token: str) -> str:
    return downloads.delivery_status(token, user_id=1, document_id="doc-1")["state"]


# --- the delivery record ---------------------------------------------------------------------------


def test_a_prepared_download_is_waiting():
    """WHEN a download has been prepared and no GET has arrived, THE SYSTEM SHALL report `waiting`."""
    assert _state(_park()["token"]) == "waiting"


def test_a_running_get_is_downloading():
    """WHILE a GET for the download is running, THE SYSTEM SHALL report `downloading`."""
    token = _park()["token"]
    downloads.delivery_started(token)
    assert _state(token) == "downloading"


def test_a_whole_get_is_complete():
    """WHEN a GET delivers the file from its first byte to its last, THE SYSTEM SHALL report `complete`."""
    token = _park()["token"]
    downloads.delivery_started(token)
    downloads.delivery_finished(token, start=0, sent=SIZE)
    assert _state(token) == "complete"
    assert downloads.delivery_status(token, user_id=1, document_id="doc-1")["size"] == SIZE


def test_a_get_that_ends_early_is_interrupted():
    """WHEN a GET ends before the last byte, THE SYSTEM SHALL report `interrupted`."""
    token = _park()["token"]
    downloads.delivery_started(token)
    downloads.delivery_finished(token, start=0, sent=400)
    assert _state(token) == "interrupted"


def test_a_resume_from_within_what_was_delivered_completes_it():
    """WHEN a Range GET starting at or before the delivered prefix reaches the end, THE SYSTEM SHALL report
    `complete` - Chrome's Resume after a cut download."""
    token = _park()["token"]
    downloads.delivery_started(token)
    downloads.delivery_finished(token, start=0, sent=400)
    downloads.delivery_started(token)
    downloads.delivery_finished(token, start=350, sent=SIZE - 350)
    assert _state(token) == "complete"


def test_a_resume_from_beyond_what_was_delivered_does_not_complete_it():
    """IF a Range GET starts past the delivered prefix, THEN THE SYSTEM SHALL NOT report `complete`: the bytes
    in between never reached the browser."""
    token = _park()["token"]
    downloads.delivery_started(token)
    downloads.delivery_finished(token, start=0, sent=400)
    downloads.delivery_started(token)
    downloads.delivery_finished(token, start=600, sent=SIZE - 600)
    assert _state(token) == "interrupted"


def test_a_download_that_never_started_before_its_link_expired_is_expired():
    """IF no GET arrived and the download's link has expired, THEN THE SYSTEM SHALL report `expired`."""
    token = _park()["token"]
    get_redis().delete(f"download:{token}")  # what the link's 5-minute expiry does
    assert _state(token) == "expired"


def test_the_delivery_record_lives_for_the_watch_window():
    """WHEN a download is prepared, THE SYSTEM SHALL keep its delivery record for download_watch_seconds."""
    token = _park()["token"]
    remaining = get_redis().ttl(f"download:{token}:delivery")
    assert get_settings().download_ttl_seconds < remaining <= get_settings().download_watch_seconds


def test_the_status_is_refused_for_another_user_or_record_or_a_malformed_token():
    """IF the requester is not the download's owner, or names another record, or a malformed token, THEN
    THE SYSTEM SHALL find nothing."""
    token = _park()["token"]
    assert downloads.delivery_status(token, user_id=2, document_id="doc-1") is None
    assert downloads.delivery_status(token, user_id=1, document_id="doc-2") is None
    assert downloads.delivery_status("not-a-token", user_id=1, document_id="doc-1") is None


# --- the measured GET keeps the record ----------------------------------------------------------------


def _scope() -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
        "extensions": {},
    }


def _parked_path(token: str) -> str:
    return os.path.join(get_settings().upload_folder, "1", "downloads", token)


async def test_the_measured_get_reports_downloading_while_it_runs():
    """WHILE the measured GET is sending, THE SYSTEM SHALL report `downloading`."""
    token = _park(3 * 65536)["token"]
    seen = []

    async def send(message):
        if message["type"] == "http.response.body" and not seen:
            seen.append(await anyio.to_thread.run_sync(_state, token))

    async def receive():
        await anyio.sleep_forever()

    response = _MeasuredFileResponse(_parked_path(token), document_id="doc-1", token=token)
    await response(_scope(), receive, send)

    assert seen == ["downloading"]
    assert _state(token) == "complete"


async def test_the_measured_get_records_a_browser_that_left_as_interrupted():
    """WHEN the browser leaves part-way, THE SYSTEM SHALL record the download as `interrupted`."""
    token = _park(10 * 65536)["token"]
    left = anyio.Event()
    bodies = 0

    async def send(message):
        nonlocal bodies
        if message["type"] == "http.response.body":
            bodies += 1
            if bodies == 2:
                left.set()
        await anyio.sleep(0)

    async def receive():
        await left.wait()
        return {"type": "http.disconnect"}

    response = _MeasuredFileResponse(_parked_path(token), document_id="doc-1", token=token)
    await response(_scope(), receive, send)

    assert _state(token) == "interrupted"


async def test_a_failing_delivery_record_never_breaks_the_download(monkeypatch, caplog):
    """IF Redis fails while the GET records its delivery, THEN THE SYSTEM SHALL still send the whole file and
    log a warning naming only the error type."""
    token = _park()["token"]

    def redis_down(*args, **kwargs):
        raise RedisConnectionError("redis is unreachable")

    monkeypatch.setattr(downloads, "delivery_started", redis_down)
    monkeypatch.setattr(downloads, "delivery_finished", redis_down)
    body = []

    async def send(message):
        if message["type"] == "http.response.body":
            body.append(message.get("body", b""))

    async def receive():
        await anyio.sleep_forever()

    with caplog.at_level(logging.WARNING, logger="app.api.downloads"):
        await _MeasuredFileResponse(_parked_path(token), document_id="doc-1", token=token)(
            _scope(), receive, send
        )

    assert sum(len(chunk) for chunk in body) == SIZE
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ConnectionError" in message for message in warnings), warnings
    assert not any(token in message for message in warnings)


# --- the status endpoint ---------------------------------------------------------------------------


async def test_the_owner_sees_a_fetched_download_as_complete(authed):
    """WHEN the owner asks for the status of a download they fetched, THE SYSTEM SHALL answer `complete` and
    its size - and nothing else (no filename)."""
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)
    before = await client.get(f"{prepared['url']}/status")
    await client.get(prepared["url"])

    after = await client.get(f"{prepared['url']}/status")

    assert before.status_code == 200 and before.json() == {
        "state": "waiting",
        "size": prepared["size"],
    }
    assert after.status_code == 200 and after.json() == {
        "state": "complete",
        "size": prepared["size"],
    }


async def test_another_user_or_an_unknown_token_gets_404(authed):
    """IF someone else asks for a download's status, or for an unknown token, THEN THE SYSTEM SHALL answer
    404 - the same as a missing record."""
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)
    unknown = await client.get(f"/api/documents/{doc_id}/downloads/{'A' * 43}/status")

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
    other = await client.get(f"{prepared['url']}/status")

    assert unknown.status_code == 404 and unknown.json() == {"detail": "not found"}
    assert other.status_code == 404


async def test_the_status_answers_503_when_redis_is_down(authed, monkeypatch):
    """IF Redis is unreachable when a status is asked for, THEN THE SYSTEM SHALL answer 503."""
    client, _ = authed
    doc_id = await _upload(client)
    prepared = await _prepare(client, doc_id)

    class _DownRedis:
        def hgetall(self, *args, **kwargs):
            raise RedisConnectionError("redis is unreachable")

        def exists(self, *args, **kwargs):
            raise RedisConnectionError("redis is unreachable")

    monkeypatch.setattr(downloads, "get_redis", lambda: _DownRedis())

    resp = await client.get(f"{prepared['url']}/status")

    assert resp.status_code == 503
    assert resp.json() == {"detail": "Downloads are unavailable right now. Please try again."}


async def test_a_head_request_is_not_a_delivery():
    """WHEN a HEAD request is made for a download, THE SYSTEM SHALL NOT count it as a delivery - it sends no
    file, so the download still reads as `waiting`."""
    token = _park()["token"]
    scope = {**_scope(), "method": "HEAD"}

    async def send(message):
        pass

    async def receive():
        await anyio.sleep_forever()

    await _MeasuredFileResponse(_parked_path(token), document_id="doc-1", token=token)(
        scope, receive, send
    )

    assert _state(token) == "waiting"
