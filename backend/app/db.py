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
