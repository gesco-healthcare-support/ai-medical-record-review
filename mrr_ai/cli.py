"""Flask CLI commands for administering accounts.

Bootstraps the first admin(s), since there is no seed script and self-registration only
ever creates ordinary accounts. Usage (with the app entrypoint ``app.py``):

    flask --app app admin grant  someone@example.com
    flask --app app admin revoke someone@example.com
    flask --app app admin list

Registered on the app in ``create_app`` via ``app.cli.add_command(admin_cli)``.
``AppGroup`` commands run inside an application context, so DB access works directly.
"""

import click
from flask.cli import AppGroup

admin_cli = AppGroup("admin", help="Grant, revoke, and list admin accounts.")


def _find_user(email):
    from mrr_ai.models import User

    user = User.query.filter_by(email=email).first()
    if user is None:
        raise click.ClickException(f"No user with email {email}")
    return user


@admin_cli.command("grant")
@click.argument("email")
def grant(email):
    """Mark the account with EMAIL as an admin."""
    from mrr_ai.extensions import db

    user = _find_user(email)
    user.is_admin = True
    db.session.commit()
    click.echo(f"Granted admin to {email}")


@admin_cli.command("revoke")
@click.argument("email")
def revoke(email):
    """Remove admin from the account with EMAIL."""
    from mrr_ai.extensions import db

    user = _find_user(email)
    user.is_admin = False
    db.session.commit()
    click.echo(f"Revoked admin from {email}")


@admin_cli.command("list")
def list_admins():
    """List the email of every admin account."""
    from mrr_ai.models import User

    admins = User.query.filter_by(is_admin=True).order_by(User.email).all()
    if not admins:
        click.echo("No admin accounts.")
        return
    for user in admins:
        click.echo(user.email)
