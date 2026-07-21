"""Admin bootstrap CLI: grant, revoke, and list admin accounts by email.

Mirrors the Flask `mrr_ai/cli.py` admin group. There is no seed script and self-registration only
creates ordinary accounts, so this bootstraps the first admin(s). Runs on a synchronous session
(an operational one-shot, not a request), toggling the `is_admin` column (== FastAPI-Users'
`is_superuser` via the model synonym).

Usage (from backend/, with the DB env set):
    python -m app.cli admin grant  someone@example.com
    python -m app.cli admin revoke someone@example.com
    python -m app.cli admin list
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import User


def _set_admin(email: str, value: bool) -> int:
    with get_sessionmaker()() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"No user with email {email}", file=sys.stderr)
            return 1
        user.is_admin = value
        session.commit()
        print(f"{'Granted admin to' if value else 'Revoked admin from'} {email}")
        return 0


def _list_admins() -> int:
    with get_sessionmaker()() as session:
        admins = session.scalars(
            select(User).where(User.is_admin.is_(True)).order_by(User.email)
        ).all()
        if not admins:
            print("No admin accounts.")
            return 0
        for user in admins:
            print(user.email)
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    groups = parser.add_subparsers(dest="group", required=True)

    admin = groups.add_parser("admin", help="Grant, revoke, and list admin accounts.")
    actions = admin.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("grant", "Mark the account with EMAIL as an admin."),
        ("revoke", "Remove admin from the account with EMAIL."),
    ):
        sub = actions.add_parser(action, help=help_text)
        sub.add_argument("email")
    actions.add_parser("list", help="List the email of every admin account.")

    args = parser.parse_args(argv)
    if args.action == "grant":
        return _set_admin(args.email, True)
    if args.action == "revoke":
        return _set_admin(args.email, False)
    return _list_admins()


if __name__ == "__main__":
    raise SystemExit(main())
