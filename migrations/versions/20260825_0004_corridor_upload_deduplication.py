"""Add corridor-photo hashes for resumable/retried bulk uploads.

Revision ID: 20260825_0004
Revises: 20260825_0003
"""
from alembic import op
import sqlalchemy as sa


revision = '20260825_0004'
down_revision = '20260825_0003'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {row['name'] for row in inspector.get_columns('corridor_photos')}
    if 'content_hash' not in columns:
        with op.batch_alter_table('corridor_photos') as batch_op:
            batch_op.add_column(sa.Column('content_hash', sa.String(64), nullable=True))
    indexes = {row['name'] for row in sa.inspect(op.get_bind()).get_indexes('corridor_photos')}
    if 'ix_corridor_photos_content_hash' not in indexes:
        op.create_index('ix_corridor_photos_content_hash', 'corridor_photos', ['content_hash'], unique=False)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    indexes = {row['name'] for row in inspector.get_indexes('corridor_photos')}
    if 'ix_corridor_photos_content_hash' in indexes:
        op.drop_index('ix_corridor_photos_content_hash', table_name='corridor_photos')
    columns = {row['name'] for row in sa.inspect(op.get_bind()).get_columns('corridor_photos')}
    if 'content_hash' in columns:
        with op.batch_alter_table('corridor_photos') as batch_op:
            batch_op.drop_column('content_hash')
