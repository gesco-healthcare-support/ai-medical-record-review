"""Unit tests for the UserManager password rule (P2b).

Server-side twin of the Flask MrrPasswordUtil: 8+ characters, at least one digit, one symbol.
A direct API register must not be able to slip a password the UI would reject.
"""

import pytest
from fastapi_users.exceptions import InvalidPasswordException

from app.auth.users import UserManager


def _manager() -> UserManager:
    # validate_password never touches user_db; None keeps the test free of DB setup.
    return UserManager(user_db=None)


async def test_rejects_too_short():
    with pytest.raises(InvalidPasswordException):
        await _manager().validate_password("Ab1!", object())


async def test_rejects_missing_digit():
    with pytest.raises(InvalidPasswordException):
        await _manager().validate_password("Password!", object())


async def test_rejects_missing_symbol():
    with pytest.raises(InvalidPasswordException):
        await _manager().validate_password("Password1", object())


async def test_accepts_strong_password():
    assert await _manager().validate_password("Str0ng#pw", object()) is None
