"""SQLAlchemy engine/session + declarative Base for the FastAPI backend.

Lazy engine (built on first use from Settings) so importing the package needs no DB. The web
tier uses a request-scoped Session via the get_db dependency; RQ workers build their own
engine/Session per process (never share a Session across threads/tasks).
"""

from collections.abc import AsyncIterator, Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all models (see app/models.py)."""


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    # pool_pre_ping guards against Postgres dropping idle connections held by long-lived
    # workers; future_style engine is the SQLAlchemy 2.0 default.
    return create_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    """The session factory. Both flags are load-bearing, so neither is a default to tidy away.

    ``expire_on_commit=False`` is what makes the segmentation pools SAFE. A Session is not
    thread-safe, and the pooled callables are careful about that - `_stored_page_text` opens its
    own short-lived session inside the worker, and `summarize_row` is handed plain values with
    the prompts resolved up front. But `_stored_page_text` also CLOSES OVER `document`, an ORM
    object belonging to the caller's session, and reads `document.id` and `document.stored_path`
    from the worker thread.

    With expiry on, every commit in the outer thread - and `report()` commits throughout
    segmentation - would mark those attributes stale, so the next read inside a worker would
    emit a SELECT on the OUTER session from another thread. That is precisely the concurrent use
    the pools are written to avoid, and it would appear as an intermittent fault under load
    rather than as an error anyone could attribute.

    ``autoflush=False`` keeps a read from flushing a half-built object mid-transaction.
    """
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped session, committed on success, rolled back on error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --- async (FastAPI-Users adapters are async-only) ------------------------------------------
# psycopg3 speaks both sync and async over the same postgresql+psycopg:// URL, so no extra driver.


@lru_cache
def get_async_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_async_engine(), expire_on_commit=False)


async def get_async_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: an async session for the FastAPI-Users adapters."""
    async with get_async_sessionmaker()() as session:
        yield session
