"""rename subscriptions to briefings, enrich sources and digest items

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-25 10:00:00.000000

Renames subscriptions → briefings, adds structured intent fields,
enriches curated_sources with per-source scraper metadata,
adds filter observability columns to digest_items.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Rename subscriptions → briefings ──────────────────────────
    op.rename_table('subscriptions', 'briefings')

    # Drop old index and FK constraint names (they reference 'subscriptions')
    op.drop_index(
        'idx_subscriptions_user',
        table_name='briefings',
        postgresql_where=sa.text('active = true'),
    )
    # Re-create index with new name
    op.create_index(
        'idx_briefings_user',
        'briefings', ['user_id'],
        unique=False,
        postgresql_where=sa.text('active = true'),
    )

    # ── 2. Add structured intent fields to briefings ─────────────────
    op.add_column('briefings', sa.Column(
        'intent_description', sa.Text(), nullable=True,
    ))
    op.add_column('briefings', sa.Column(
        'keywords', postgresql.JSONB(astext_type=sa.Text()),
        server_default='[]', nullable=False,
    ))
    op.add_column('briefings', sa.Column(
        'example_articles', postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ))
    op.add_column('briefings', sa.Column(
        'exclusion_keywords', postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ))

    # ── 3. Update digests FK: subscription_id → briefing_id ──────────
    # Drop old index
    op.drop_index('idx_digests_subscription', table_name='digests')
    # Drop old FK
    op.drop_constraint(
        'digests_subscription_id_fkey', 'digests', type_='foreignkey',
    )
    # Rename column
    op.alter_column('digests', 'subscription_id',
                     new_column_name='briefing_id')
    # Re-create FK pointing to briefings
    op.create_foreign_key(
        'fk_digests_briefing_id',
        'digests', 'briefings',
        ['briefing_id'], ['id'],
    )
    # Re-create index
    op.create_index(
        'idx_digests_briefing',
        'digests', ['briefing_id', sa.text('created_at DESC')],
        unique=False,
    )

    # ── 4. Enrich curated_sources ────────────────────────────────────
    # Rename rss_url → url
    op.alter_column('curated_sources', 'rss_url',
                     new_column_name='url')
    # Add new metadata columns
    op.add_column('curated_sources', sa.Column(
        'source_type', sa.String(length=20), server_default='rss', nullable=False,
    ))
    op.add_column('curated_sources', sa.Column(
        'category', sa.String(length=50), nullable=True,
    ))
    op.add_column('curated_sources', sa.Column(
        'tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
    ))
    op.add_column('curated_sources', sa.Column(
        'description', sa.Text(), nullable=True,
    ))
    op.add_column('curated_sources', sa.Column(
        'scraper_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
    ))

    # ── 5. Add filter observability columns to digest_items ──────────
    op.add_column('digest_items', sa.Column(
        'heuristic_score', sa.Float(), nullable=True,
    ))
    op.add_column('digest_items', sa.Column(
        'llm_relevance_score', sa.Integer(), nullable=True,
    ))
    op.add_column('digest_items', sa.Column(
        'llm_relevance_reason', sa.Text(), nullable=True,
    ))


def downgrade() -> None:
    # ── 5. Drop filter columns from digest_items ─────────────────────
    op.drop_column('digest_items', 'llm_relevance_reason')
    op.drop_column('digest_items', 'llm_relevance_score')
    op.drop_column('digest_items', 'heuristic_score')

    # ── 4. Revert curated_sources ────────────────────────────────────
    op.drop_column('curated_sources', 'scraper_config')
    op.drop_column('curated_sources', 'description')
    op.drop_column('curated_sources', 'tags')
    op.drop_column('curated_sources', 'category')
    op.drop_column('curated_sources', 'source_type')
    op.alter_column('curated_sources', 'url',
                     new_column_name='rss_url')

    # ── 3. Revert digests FK ─────────────────────────────────────────
    op.drop_index('idx_digests_briefing', table_name='digests')
    op.drop_constraint('fk_digests_briefing_id', 'digests', type_='foreignkey')
    op.alter_column('digests', 'briefing_id',
                     new_column_name='subscription_id')
    op.create_foreign_key(
        'digests_subscription_id_fkey',
        'digests', 'subscriptions',
        ['subscription_id'], ['id'],
    )
    op.create_index(
        'idx_digests_subscription',
        'digests', ['subscription_id', sa.text('created_at DESC')],
        unique=False,
    )

    # ── 2. Drop intent fields from briefings ─────────────────────────
    op.drop_column('briefings', 'exclusion_keywords')
    op.drop_column('briefings', 'example_articles')
    op.drop_column('briefings', 'keywords')
    op.drop_column('briefings', 'intent_description')

    # ── 1. Rename briefings → subscriptions ──────────────────────────
    op.drop_index(
        'idx_briefings_user',
        table_name='briefings',
        postgresql_where=sa.text('active = true'),
    )
    op.create_index(
        'idx_subscriptions_user',
        'briefings', ['user_id'],
        unique=False,
        postgresql_where=sa.text('active = true'),
    )
    op.rename_table('briefings', 'subscriptions')
