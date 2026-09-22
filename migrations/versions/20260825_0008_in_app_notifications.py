"""Add lightweight in-app notifications.

Revision ID: 20260825_0008
Revises: 20260825_0007
"""
from alembic import op
import sqlalchemy as sa

revision = '20260825_0008'
down_revision = '20260825_0007'
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('user_notifications'):
        op.create_table('user_notifications',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('category', sa.String(40), nullable=False),
            sa.Column('title', sa.String(180), nullable=False),
            sa.Column('message', sa.String(500)), sa.Column('link_url', sa.String(500)),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(), nullable=False))
        op.create_index('ix_user_notifications_user_id', 'user_notifications', ['user_id'])
        op.create_index('ix_user_notifications_is_read', 'user_notifications', ['is_read'])
        op.create_index('ix_user_notifications_created_at', 'user_notifications', ['created_at'])


def downgrade():
    if sa.inspect(op.get_bind()).has_table('user_notifications'):
        op.drop_table('user_notifications')
