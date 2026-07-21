"""Unit tests for the Flask-Security-compatible password helper (P2a).

The security-critical guarantees:
- new passwords hash + verify round-trip;
- a wrong password fails;
- verify_and_update NEVER returns an updated hash (so migrated Flask-Security hashes are never
  silently rewritten into a different format, which would lock existing users out on next login);
- the helper verifies a hash built the way Flask-Security-Too builds one -- argon2id over
  base64(HMAC-SHA512(salt, password)) -- proving byte-compatibility with the migrated hashes.
"""

import base64
import hashlib
import hmac

from argon2 import PasswordHasher

from app.auth.password import MrrPasswordHelper

_SALT = "dev-only-salt"
_PW = "Str0ng#pw"


def _flask_security_hash(password: str, salt: str) -> str:
    """Independently reproduce Flask-Security-Too's hash construction (a different code path
    from the helper) so the verify check is a genuine cross-implementation test, not a tautology.
    argon2 params match the argon2-cffi/passlib defaults the existing hashes were made with."""
    pre = base64.b64encode(
        hmac.new(salt.encode("utf-8"), password.encode("utf-8"), hashlib.sha512).digest()
    )
    return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4).hash(pre)


def test_hash_then_verify_round_trips():
    helper = MrrPasswordHelper(salt=_SALT)
    hashed = helper.hash(_PW)
    verified, updated = helper.verify_and_update(_PW, hashed)
    assert verified is True
    assert updated is None  # never re-hash


def test_wrong_password_fails():
    helper = MrrPasswordHelper(salt=_SALT)
    hashed = helper.hash(_PW)
    verified, updated = helper.verify_and_update("not-the-password", hashed)
    assert verified is False
    assert updated is None


def test_verifies_a_flask_security_style_hash():
    helper = MrrPasswordHelper(salt=_SALT)
    external = _flask_security_hash(_PW, _SALT)
    verified, updated = helper.verify_and_update(_PW, external)
    assert verified is True
    assert updated is None


def test_wrong_salt_does_not_verify():
    external = _flask_security_hash(_PW, _SALT)
    verified, _ = MrrPasswordHelper(salt="a-different-salt").verify_and_update(_PW, external)
    assert verified is False


def test_generate_is_random_and_nonempty():
    helper = MrrPasswordHelper(salt=_SALT)
    a, b = helper.generate(), helper.generate()
    assert a and b and a != b
