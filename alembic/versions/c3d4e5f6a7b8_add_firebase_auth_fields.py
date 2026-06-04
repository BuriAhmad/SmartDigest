"""add firebase auth fields

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-04 06:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("firebase_uid", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_users_firebase_uid",
        "users",
        ["firebase_uid"],
        unique=True,
        postgresql_where=sa.text("firebase_uid IS NOT NULL"),
    )
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=128),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE users SET password_hash = 'FIREBASE_AUTH_DOWNGRADE_PLACEHOLDER' "
        "WHERE password_hash IS NULL"
    )
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.drop_index("ix_users_firebase_uid", table_name="users")
    op.drop_column("users", "firebase_uid")
