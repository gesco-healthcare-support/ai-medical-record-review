"""add access_token table and is_verified

Revision ID: a0725d467a48
Revises: 73abdcd5ef01
Create Date: 2026-07-15 09:40:23.118782

FastAPI-Users (P2): the access_token table backs the cookie DatabaseStrategy (opaque, server-side,
revocable sessions); is_verified is required by the FastAPI-Users user model. Additive only --
existing rows keep their password/active/is_admin values (aliased in the model as
hashed_password/is_active/is_superuser). Registration is non-confirmable, so is_verified stays
False and is never gated on.
"""

from typing import Sequence, Union

from alembic import op
import fastapi_users_db_sqlalchemy.generics
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a0725d467a48"
down_revision: Union[str, Sequence[str], None] = "73abdcd5ef01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "access_token",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=43), nullable=False),
        sa.Column(
            "created_at",
            fastapi_users_db_sqlalchemy.generics.TIMESTAMPAware(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index(
        op.f("ix_access_token_created_at"), "access_token", ["created_at"], unique=False
    )
    # NOT NULL on a populated table needs a default to backfill existing rows; drop the
    # server_default afterward so the column matches the model (application-side default only).
    op.add_column(
        "user",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("user", "is_verified", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user", "is_verified")
    op.drop_index(op.f("ix_access_token_created_at"), table_name="access_token")
    op.drop_table("access_token")
