"""The admin bootstrap CLI (app/cli.py).

There is no seed script and self-registration only creates ordinary accounts, so this CLI is how the
first admin exists at all - and until now nothing exercised it. It runs on the same synchronous
sessionmaker the API tests use, so it reaches the same Postgres.
"""

from sqlalchemy import select

from app.cli import main
from app.db import get_sessionmaker
from app.models import User


def _is_admin(email: str) -> bool:
    with get_sessionmaker()() as session:
        return bool(session.scalar(select(User).where(User.email == email)).is_admin)


def test_list_exits_zero():
    """WHEN `admin list` runs, THE SYSTEM SHALL exit 0.

    Listing has no failure mode, which is why the helper returns nothing and `main` supplies the
    exit code. Pinned so that split cannot change what the shell sees.
    """
    assert main(["admin", "list"]) == 0


def test_grant_then_revoke_round_trips(seeded_user):
    """WHEN an account is granted and then revoked, THE SYSTEM SHALL exit 0 each time and leave the
    admin flag exactly as it started."""
    email, _password = seeded_user
    assert _is_admin(email) is False

    assert main(["admin", "grant", email]) == 0
    assert _is_admin(email) is True

    assert main(["admin", "revoke", email]) == 0
    assert _is_admin(email) is False


def test_an_unknown_email_exits_nonzero(capsys):
    """WHEN the email matches no account, THE SYSTEM SHALL exit 1 and say so on stderr.

    The non-zero code is the half that matters: this is a bootstrap step someone runs from a shell
    script, where a silent success on a typo'd address would look like the admin was created.
    """
    assert main(["admin", "grant", "no-such-account@example.com"]) == 1
    assert "no-such-account@example.com" in capsys.readouterr().err
