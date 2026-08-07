"""Shared pytest fixtures for the backend.

The integration tests drive the real ASGI app (httpx AsyncClient over ASGITransport) against the
docker Postgres from docker-compose.dev.yml. `alembic upgrade head` must have run so the schema
exists (CI runs it; the local dev DB is already migrated). Test accounts use a unique email prefix
and are removed before and after every test, so runs are idempotent and never touch real data.
"""

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator

import re
import socket
from pathlib import Path

import pytest


# Dev-only defaults so `uv run pytest` works without an inline export; real env (CI, prod) wins via
# setdefault. These are the published local docker credentials (docker-compose.dev.yml), never a
# production secret. Set BEFORE importing app.* so the cached get_settings() reads them.
#
# connect_timeout is load-bearing, not cosmetic: psycopg blocks indefinitely on a TCP connect, so
# without it a missing or misconfigured database makes the whole suite HANG with no output at all
# (pytest buffers, so a Ctrl-C or a kill loses even the header). Two separate sessions lost hours to
# that silence, each concluding "pytest is broken". Five seconds turns it into a legible
# OperationalError naming the host, port and reason.
def _compose_postgres_ports() -> list[tuple[int, str]]:
    """(host_port, password_default) for every compose file that publishes Postgres.

    Module-level so BOTH the auto-discovery below and the explicit-URL check share one parse. When
    they were separate, "which port is the app stack" could be answered two different ways.
    """
    root = Path(__file__).resolve().parents[2]
    candidates: list[tuple[int, str]] = []
    for name in ("docker-compose.yml", "docker-compose.dev.yml"):
        path = root / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        block = re.search(r"^\s{2}postgres:.*?(?=^\s{2}\S|\Z)", text, re.S | re.M)
        if not block:
            continue
        section = block.group(0)
        port = re.search(r'"(\d+):5432"', section)
        # Either a literal (dev) or a ${VAR:-default} substitution (main).
        pw = re.search(r"POSTGRES_PASSWORD:\s*(?:\$\{[A-Z_]+:-([^}]+)\}|(\S+))", section)
        if port:
            candidates.append((int(port.group(1)), (pw.group(1) or pw.group(2)) if pw else ""))
    return candidates


def _local_database_url() -> str:
    """Point the suite at whichever local Postgres is actually RUNNING.

    Hardcoding this broke once already and would again: the two compose files deliberately publish
    DIFFERENT host ports so they can run side by side - docker-compose.dev.yml on 5432 with password
    mrr_dev_only, docker-compose.yml on 5433 with ${POSTGRES_PASSWORD:-mrr_local_only}. A constant
    is therefore correct for one stack and wrong for the other, and which is right depends on what
    happens to be up. That cost a debugging detour on 2026-08-06: every DB-touching test failed on
    connection before running a single assertion.

    So derive it instead. The compose files are the source of truth for both the port and the
    password default, so changing a port there needs no change here.
    """
    root = Path(__file__).resolve().parents[2]
    candidates = _compose_postgres_ports()

    # An explicit POSTGRES_PASSWORD in .env overrides the compose default for whichever stack uses
    # substitution. Read, never logged.
    env_pw = ""
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("POSTGRES_PASSWORD="):
                env_pw = line.split("=", 1)[1].strip().strip("\"'")
                break

    # The DEV stack is the tests' database BY DESIGN - a throwaway. docker-compose.yml is the running
    # APPLICATION's database, and pointing the suite at it is actively harmful, not merely wrong:
    # these fixtures INSERT users, documents and tokens, and a database that already holds data
    # produces foreign-key violations and StaleDataError that look like product bugs. Measured
    # 2026-08-06: aiming the suite at the app database turned ~30 passing tests into failures that
    # had nothing to do with the code under test.
    #
    # So prefer dev, and if it is not up say so plainly rather than silently borrowing the app's
    # database. A missing test database is an operator problem with a one-line fix; a suite quietly
    # writing into the running app's data is a much worse failure to debug.
    dev = next((c for c in candidates if c[0] == 5432), None)
    ordered = [dev] if dev else []
    ordered += [c for c in candidates if c is not dev]

    for port, default_pw in ordered:
        with socket.socket() as sock:
            sock.settimeout(0.4)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                continue
        if dev and port != dev[0]:
            raise RuntimeError(
                f"The test database (docker-compose.dev.yml, port {dev[0]}) is not running, and "
                f"port {port} belongs to the APPLICATION stack (docker-compose.yml). Refusing to "
                f"run the suite against it - the fixtures insert and delete rows.\n"
                f"Start the test database with:  docker compose -f docker-compose.dev.yml up -d postgres"
            )
        password = env_pw or default_pw or "mrr_dev_only"
        return f"postgresql+psycopg://mrr:{password}@localhost:{port}/mrr?connect_timeout=5"

    # Nothing listening at all: keep a concrete default so the error names a port and reason.
    fallback_port = ordered[0][0] if ordered else 5432
    return (
        f"postgresql+psycopg://mrr:{env_pw or 'mrr_dev_only'}@localhost:"
        f"{fallback_port}/mrr?connect_timeout=5"
    )


def _reject_the_app_database(url: str) -> None:
    """Refuse an EXPLICIT DATABASE_URL that points at the running application's Postgres.

    `setdefault` below only computes a safe URL when DATABASE_URL is UNSET. Anyone who exports it -
    a shell profile, a CI script, a developer pasting a connection string - bypasses every check in
    `_local_database_url` and aims these fixtures at whatever they named.

    That is not hypothetical. On 2026-08-07 a full session's worth of runs went against the
    application database on 5433 because DATABASE_URL was exported on each invocation. The fixtures
    inserted and deleted roughly 4,400 rows in it, left an orphan document and a `pytest-*` user
    behind, and - worse for the humans involved - produced five failures that were diagnosed as a
    product defect, fixed, and merged before anyone noticed the tests had been reading real data.

    So the check runs on the FINAL url, whatever its source. Explicitly naming the app stack is far
    more likely to be a mistake than an intention; anyone who truly means it can point at the port
    directly with MRR_ALLOW_APP_DATABASE=1 and own the consequence.
    """
    if os.environ.get("MRR_ALLOW_APP_DATABASE") == "1":
        return
    port = re.search(r":(\d+)/", url)
    if not port:
        return
    candidates = dict(_compose_postgres_ports())
    dev = 5432 if 5432 in candidates else None
    if dev is not None and int(port.group(1)) in candidates and int(port.group(1)) != dev:
        raise RuntimeError(
            f"DATABASE_URL names port {port.group(1)}, which belongs to the APPLICATION stack "
            "(docker-compose.yml). These fixtures insert and delete rows - refusing.\n"
            "Unset DATABASE_URL and the suite finds the test database itself, or start it with:\n"
            "  docker compose -f docker-compose.dev.yml up -d postgres\n"
            "To override deliberately: MRR_ALLOW_APP_DATABASE=1"
        )


os.environ.setdefault("DATABASE_URL", _local_database_url())
_reject_the_app_database(os.environ["DATABASE_URL"])
os.environ.setdefault("SECRET_KEY", "dev-only-secret")
os.environ.setdefault("SECURITY_PASSWORD_SALT", "dev-only-salt")
os.environ.setdefault("ENVIRONMENT", "dev")

# psycopg3's async mode cannot run on Windows' default ProactorEventLoop; select the
# SelectorEventLoop policy so the async DB works under pytest here. No-op on Linux (CI, prod),
# whose default loop already drives psycopg async fine.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from httpx import ASGITransport, AsyncClient  # noqa: E402 - env must be set before app import
from sqlalchemy import delete, select  # noqa: E402

from app.auth.password import MrrPasswordHelper  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AccessToken,
    AuditLog,
    Document,
    Job,
    ReviewRow,
    SegmentRow,
    Summary,
    User,
)

TEST_EMAIL_PREFIX = "pytest-auth-"


def unique_test_email() -> str:
    # example.com is the RFC 2606 reserved example domain (synthetic, and accepted by EmailStr;
    # reserved TLDs like .test/.invalid/.localhost are rejected by the email validator).
    return f"{TEST_EMAIL_PREFIX}{uuid.uuid4().hex}@example.com"


def _delete_test_users() -> None:
    """Remove every account the integration tests created plus everything it owns, so runs are
    idempotent. Core deletes in explicit FK order (child tables first) - more robust than relying
    on ORM cascade ordering across a multi-document flush."""
    with get_sessionmaker()() as session:
        ids = session.scalars(select(User.id).where(User.email.like(TEST_EMAIL_PREFIX + "%"))).all()
        if not ids:
            return
        doc_ids = select(Document.id).where(Document.user_id.in_(ids)).scalar_subquery()
        job_ids = select(Job.id).where(Job.document_id.in_(doc_ids)).scalar_subquery()
        session.execute(delete(SegmentRow).where(SegmentRow.job_id.in_(job_ids)))
        session.execute(delete(Summary).where(Summary.document_id.in_(doc_ids)))
        session.execute(delete(ReviewRow).where(ReviewRow.document_id.in_(doc_ids)))
        session.execute(delete(Job).where(Job.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.user_id.in_(ids)))
        session.execute(delete(AuditLog).where(AuditLog.user_id.in_(ids)))
        session.execute(delete(AccessToken).where(AccessToken.user_id.in_(ids)))
        session.execute(delete(User).where(User.id.in_(ids)))
        session.commit()


@pytest.fixture(autouse=True)
def _clean_test_users() -> Iterator[None]:
    _delete_test_users()
    yield
    _delete_test_users()


@pytest.fixture
def seeded_user() -> tuple[str, str]:
    """A verifiable dev-salt account inserted directly (bypassing register), so login is tested
    against a hash built exactly the way the migrated Flask-Security hashes were."""
    email = unique_test_email()
    password = "Seeded#pw1"
    with get_sessionmaker()() as session:
        session.add(
            User(
                email=email,
                name="Seed User",
                password=MrrPasswordHelper().hash(password),
                active=True,
            )
        )
        session.commit()
    return email, password


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def lane_queues(kind: str):
    """Every RQ queue a job of this kind could land on: the base plus each per-user lane.

    Jobs are routed onto their owner's lane (app/worker/queues.queue_for), so a test that only wants
    to assert "this was enqueued" must not pin one lane name - it would pass or fail depending on
    which user the fixture happened to create. Tests that care about WHICH lane assert on the name
    directly instead.
    """
    from rq import Queue

    from app.worker.queues import base_queue_name, get_redis

    base = base_queue_name(kind)
    return [
        q
        for q in Queue.all(connection=get_redis())
        if q.name == base or q.name.startswith(base + ":")
    ]


def empty_lanes(kind: str) -> None:
    for queue in lane_queues(kind):
        queue.empty()


def lane_count(kind: str) -> int:
    return sum(queue.count for queue in lane_queues(kind))


def lane_jobs(kind: str) -> list:
    return [job for queue in lane_queues(kind) for job in queue.jobs]


class LaneGroup:
    """All lanes for one kind, exposing the slice of the RQ Queue API these tests use.

    Lets a test say "a job was enqueued for this kind" without naming a lane, which is what those
    tests actually mean; the lane-routing behaviour itself is asserted directly in test_jobs.py.
    """

    def __init__(self, kind: str):
        self.kind = kind

    def empty(self) -> None:
        empty_lanes(self.kind)

    @property
    def count(self) -> int:
        return lane_count(self.kind)

    @property
    def jobs(self) -> list:
        return lane_jobs(self.kind)


def lanes(kind: str) -> LaneGroup:
    return LaneGroup(kind)
