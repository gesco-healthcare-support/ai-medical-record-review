"""Unit tests for the Flask-Security-compatible password helper (P2a).

The security-critical guarantees:
- new passwords hash + verify round-trip;
- a wrong password fails;
- verify_and_update NEVER returns an updated hash (so migrated Flask-Security hashes are never
  silently rewritten into a different format, which would lock existing users out on next login);
- the helper verifies a hash built the way Flask-Security-Too builds one -- argon2id over
  base64(HMAC-SHA512(salt, password)) -- proving byte-compatibility with the migrated hashes;
- the PRODUCTION hasher's cost is pinned: the suite hashes cheaply, so nothing else would notice it
  change.
"""

import base64
import hashlib
import hmac

from argon2 import PasswordHasher, Type, extract_parameters, profiles

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
    assert a
    assert b
    assert a != b


def test_the_production_hasher_keeps_the_flask_security_cost():
    """conftest swaps the default hasher for a cheap one, so no other test would notice a change to
    the PRODUCTION cost - the cost every new user's password is hashed at. Verification reads the
    cost from each stored hash, so the migrated Flask-Security hashes verify at any setting: raising
    the cost is safe (update this pin with it); lowering it weakens every new user's hash and needs
    a security reason."""
    from tests.conftest import PRODUCTION_HASHER

    got = {
        "type": PRODUCTION_HASHER.type,
        "time_cost": PRODUCTION_HASHER.time_cost,
        "memory_cost": PRODUCTION_HASHER.memory_cost,
        "parallelism": PRODUCTION_HASHER.parallelism,
    }
    assert got == {"type": Type.ID, "time_cost": 3, "memory_cost": 65536, "parallelism": 4}, (
        "the production password hasher no longer uses the Flask-Security cost"
    )


def test_the_suite_hashes_at_the_cheapest_cost():
    """Hashing at production cost was 43% of backend test time (562 hashes and verifies, measured
    2026-09-22). Reads the parameters back out of a hash made through the default path, so it fails
    if the swap is removed OR if the helper stops using the default hasher."""
    stored = MrrPasswordHelper(salt=_SALT).hash(_PW)
    assert extract_parameters(stored) == profiles.CHEAPEST, (
        "the suite is hashing test passwords at more than the cheapest cost"
    )
