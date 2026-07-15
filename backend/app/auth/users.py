"""The FastAPI-Users UserManager: our Flask-Security password rule + password helper.

Uses MrrPasswordHelper (the HMAC-SHA512 -> argon2id scheme) for verify + hash, and reproduces the
Flask MrrPasswordUtil rule (8+ chars, a digit, a symbol) in validate_password so a direct API
register cannot bypass what the UI enforces. Reset/verification token secrets come from SECRET_KEY.
"""

import re
from collections.abc import AsyncIterator

from fastapi import Depends
from fastapi_users import BaseUserManager, IntegerIDMixin
from fastapi_users.exceptions import InvalidPasswordException

from app.auth.db import get_user_db
from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.models import User

_DIGIT = re.compile(r"\d")
_SYMBOL = re.compile(r"[^A-Za-z0-9]")


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    def __init__(self, user_db) -> None:
        super().__init__(user_db, password_helper=MrrPasswordHelper())
        # Secrets for the reset-password / verification token JWTs (P2c). Read here, not at import,
        # so importing the module needs no environment.
        secret = get_settings().secret_key
        self.reset_password_token_secret = secret
        self.verification_token_secret = secret

    async def validate_password(self, password: str, user) -> None:
        problems = []
        if len(password) < 8:
            problems.append("at least 8 characters")
        if not _DIGIT.search(password):
            problems.append("a number")
        if not _SYMBOL.search(password):
            problems.append("a symbol")
        if problems:
            raise InvalidPasswordException(reason="Password must contain " + ", ".join(problems))


async def get_user_manager(user_db=Depends(get_user_db)) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)
