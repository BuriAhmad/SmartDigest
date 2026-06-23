"""add digest item date metadata

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "digest_items",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "digest_items",
        sa.Column("date_source", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "digest_items",
        sa.Column("date_confidence", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "digest_items",
        sa.Column("date_resolution_status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "digest_items",
        sa.Column("date_candidates_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("digest_items", "date_candidates_json")
    op.drop_column("digest_items", "date_resolution_status")
    op.drop_column("digest_items", "date_confidence")
    op.drop_column("digest_items", "date_source")
    op.drop_column("digest_items", "updated_at")
