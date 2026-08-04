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
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr?connect_timeout=5",
)
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
    PageText,
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
        session.execute(delete(PageText).where(PageText.document_id.in_(doc_ids)))
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
