"""add users table, migrate subscriptions from api_key_id to user_id

Revision ID: a1b2c3d4e5f6
Revises: 320e94e3b394
Create Date: 2026-03-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '320e94e3b394'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=128), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('plan', sa.String(length=20), server_default='free', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # 2. Add user_id column to subscriptions (nullable at first for migration)
    op.add_column('subscriptions', sa.Column('user_id', sa.Integer(), nullable=True))

    # 3. Create a default migration user to own orphaned subscriptions
    #    This ensures existing data isn't lost during migration.
    op.execute(
        "INSERT INTO users (email, password_hash, name, plan) "
        "VALUES ('migrated@smartdigest.local', 'MIGRATION_PLACEHOLDER_NOT_A_REAL_HASH', 'Migrated User', 'free') "
        "ON CONFLICT (email) DO NOTHING"
    )
    # Point all existing subscriptions to this migration user
    op.execute(
        "UPDATE subscriptions SET user_id = (SELECT id FROM users WHERE email = 'migrated@smartdigest.local') "
        "WHERE user_id IS NULL"
    )

    # 4. Make user_id NOT NULL now that all rows have a value
    op.alter_column('subscriptions', 'user_id', nullable=False)

    # 5. Add FK constraint and index
    op.create_foreign_key(
        'fk_subscriptions_user_id',
        'subscriptions', 'users',
        ['user_id'], ['id'],
    )
    op.create_index(
        'idx_subscriptions_user',
        'subscriptions', ['user_id'],
        unique=False,
        postgresql_where=sa.text('active = true'),
    )

    # 6. Drop old api_key_id column and its index
    op.drop_index('idx_subscriptions_api_key', table_name='subscriptions', postgresql_where=sa.text('active = true'))
    op.drop_constraint('subscriptions_api_key_id_fkey', 'subscriptions', type_='foreignkey')
    op.drop_column('subscriptions', 'api_key_id')

    # 7. Drop api_keys table
    op.drop_index(op.f('ix_api_keys_key_hash'), table_name='api_keys')
    op.drop_table('api_keys')


def downgrade() -> None:
    # Recreate api_keys table
    op.create_table(
        'api_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('prefix', sa.String(length=4), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column('api_call_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_api_keys_key_hash'), 'api_keys', ['key_hash'], unique=True)

    # Add back api_key_id to subscriptions
    op.add_column('subscriptions', sa.Column('api_key_id', sa.Integer(), nullable=True))
    # Can't restore old FK values, set to NULL
    op.create_index(
        'idx_subscriptions_api_key',
        'subscriptions', ['api_key_id'],
        unique=False,
        postgresql_where=sa.text('active = true'),
    )

    # Drop user_id column and index
    op.drop_index('idx_subscriptions_user', table_name='subscriptions', postgresql_where=sa.text('active = true'))
    op.drop_constraint('fk_subscriptions_user_id', 'subscriptions', type_='foreignkey')
    op.drop_column('subscriptions', 'user_id')

    # Drop users table
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
