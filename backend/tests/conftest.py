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
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr")
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
from app.models import AccessToken, User  # noqa: E402

TEST_EMAIL_PREFIX = "pytest-auth-"


def unique_test_email() -> str:
    # example.com is the RFC 2606 reserved example domain (synthetic, and accepted by EmailStr;
    # reserved TLDs like .test/.invalid/.localhost are rejected by the email validator).
    return f"{TEST_EMAIL_PREFIX}{uuid.uuid4().hex}@example.com"


def _delete_test_users() -> None:
    """Remove every account the integration tests created plus its sessions. The access_token FK is
    ondelete=cascade, but we clear tokens explicitly too so a stray row can never block the delete."""
    with get_sessionmaker()() as session:
        ids = session.scalars(select(User.id).where(User.email.like(TEST_EMAIL_PREFIX + "%"))).all()
        if ids:
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
