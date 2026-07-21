"""FastAPI-Users database adapters: the user store and the access-token session store.

Both run on the async session (get_async_db); FastAPI-Users' SQLAlchemy adapters are async-only.
"""

from collections.abc import AsyncIterator

from fastapi import Depends
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.models import AccessToken, User


async def get_user_db(
    session: AsyncSession = Depends(get_async_db),
) -> AsyncIterator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User)


async def get_access_token_db(
    session: AsyncSession = Depends(get_async_db),
) -> AsyncIterator[SQLAlchemyAccessTokenDatabase]:
    yield SQLAlchemyAccessTokenDatabase(session, AccessToken)
