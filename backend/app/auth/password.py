"""Password hashing for FastAPI-Users, reproducing the Flask-Security-Too scheme byte-for-byte.

Flask-Security HMACs the password with SECURITY_PASSWORD_SALT (HMAC-SHA512, base64-encoded)
BEFORE argon2id, so the migrated hashes only verify against that construction -- a raw-password
argon2 verify mismatches (confirmed by the P2a probe). We implement PasswordHelperProtocol so
FastAPI-Users' UserManager uses this scheme for both verify (existing hashes) and hash (new
passwords).

verify_and_update INTENTIONALLY never returns an updated hash. FastAPI-Users would otherwise
re-hash a "deprecated" hash on login and rewrite the DB; that would convert a migrated
Flask-Security hash into pwdlib's default format and lock the user out on their next login.
Returning (verified, None) keeps every stored hash exactly as migrated.
"""

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi_users.password import PasswordHelperProtocol

from app.config import get_settings

# argon2id parameters matching the argon2-cffi/passlib config the existing Flask-Security hashes
# were produced with (m=65536 KiB, t=3, p=4). Verification reads params from the stored hash, so
# these only govern the hashing of NEW passwords.
_DEFAULT_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


class MrrPasswordHelper(PasswordHelperProtocol):
    """Flask-Security-compatible password helper (HMAC-SHA512 pre-hash, then argon2id)."""

    def __init__(self, salt: str | None = None, hasher: PasswordHasher | None = None) -> None:
        # Falls back to the configured SECURITY_PASSWORD_SALT; injectable for tests and so the
        # real production salt can be supplied at cutover without code changes.
        self._salt = salt if salt is not None else get_settings().security_password_salt
        self._hasher = hasher or _DEFAULT_HASHER

    def _pre_hash(self, password: str) -> bytes:
        """base64(HMAC-SHA512(salt, password)) -- the exact value Flask-Security feeds to argon2."""
        digest = hmac.new(
            self._salt.encode("utf-8"), password.encode("utf-8"), hashlib.sha512
        ).digest()
        return base64.b64encode(digest)

    def verify_and_update(
        self, plain_password: str, hashed_password: str
    ) -> tuple[bool, str | None]:
        try:
            self._hasher.verify(hashed_password, self._pre_hash(plain_password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, None
        # Never return an updated hash: migrated Flask-Security hashes must stay byte-identical.
        return True, None

    def hash(self, password: str) -> str:
        return self._hasher.hash(self._pre_hash(password))

    def generate(self) -> str:
        # Used by FastAPI-Users when it needs a random password (e.g. OAuth account creation).
        return secrets.token_urlsafe(32)
