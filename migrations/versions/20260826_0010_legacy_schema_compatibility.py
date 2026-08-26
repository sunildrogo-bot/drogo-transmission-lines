"""Add model columns missing from adopted legacy installations.

Revision ID: 20260826_0010
Revises: 20260825_0009
"""
from alembic import op
import sqlalchemy as sa


revision = '20260826_0010'
down_revision = '20260825_0009'
branch_labels = None
depends_on = None


def _columns(table_name):
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {row['name'] for row in inspector.get_columns(table_name)}


def _indexes(table_name):
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {row['name'] for row in inspector.get_indexes(table_name)}


def upgrade():
    if 'reset_token' not in _columns('users'):
        op.add_column('users', sa.Column('reset_token', sa.String(64), nullable=True))
    if 'reset_token_expires' not in _columns('users'):
        op.add_column('users', sa.Column('reset_token_expires', sa.DateTime(), nullable=True))
    if (
        'reset_token' in _columns('users')
        and 'ix_users_reset_token' not in _indexes('users')
    ):
        op.create_index('ix_users_reset_token', 'users', ['reset_token'], unique=False)

    if 'thumbnail_path' not in _columns('corridor_photos'):
        op.add_column(
            'corridor_photos',
            sa.Column('thumbnail_path', sa.String(255), nullable=True),
        )

    if 'defect_copy_path' not in _columns('tower_photos'):
        op.add_column(
            'tower_photos',
            sa.Column('defect_copy_path', sa.String(255), nullable=True),
        )
    if 'raw_deleted' not in _columns('tower_photos'):
        op.add_column(
            'tower_photos',
            sa.Column(
                'raw_deleted',
                sa.Boolean(),
                nullable=True,
                server_default=sa.false(),
            ),
        )
    if 'thumbnail_path' not in _columns('tower_photos'):
        op.add_column(
            'tower_photos',
            sa.Column('thumbnail_path', sa.String(255), nullable=True),
        )

    if 'resolved_by' not in _columns('tower_defects'):
        op.add_column(
            'tower_defects',
            sa.Column('resolved_by', sa.String(120), nullable=True),
        )
    if 'resolved_at' not in _columns('tower_defects'):
        op.add_column(
            'tower_defects',
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
        )
    if 'resolution_comment' not in _columns('tower_defects'):
        op.add_column(
            'tower_defects',
            sa.Column('resolution_comment', sa.Text(), nullable=True),
        )


def downgrade():
    # These columns predate Alembic on many installations. Removing them would
    # destroy user reset data, thumbnails, and defect-resolution history, so a
    # downgrade intentionally preserves them.
    pass

