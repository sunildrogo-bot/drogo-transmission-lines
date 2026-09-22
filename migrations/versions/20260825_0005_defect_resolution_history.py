"""Add immutable defect close/reopen history and rectification evidence.

Revision ID: 20260825_0005
Revises: 20260825_0004
"""
from alembic import op
import sqlalchemy as sa


revision = '20260825_0005'
down_revision = '20260825_0004'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('defect_resolution_events'):
        op.create_table(
            'defect_resolution_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('defect_id', sa.Integer(), sa.ForeignKey('tower_defects.id', ondelete='CASCADE'), nullable=False),
            sa.Column('action', sa.String(20), nullable=False),
            sa.Column('from_status', sa.String(20), nullable=False),
            sa.Column('to_status', sa.String(20), nullable=False),
            sa.Column('comment', sa.Text(), nullable=False),
            sa.Column('evidence_image_path', sa.String(255), nullable=True),
            sa.Column('changed_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('changed_by_name', sa.String(120), nullable=True),
            sa.Column('changed_by_role', sa.String(40), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
    indexes = {row['name'] for row in sa.inspect(op.get_bind()).get_indexes('defect_resolution_events')}
    if 'ix_defect_resolution_events_defect_id' not in indexes:
        op.create_index('ix_defect_resolution_events_defect_id', 'defect_resolution_events', ['defect_id'])


def downgrade():
    if sa.inspect(op.get_bind()).has_table('defect_resolution_events'):
        op.drop_table('defect_resolution_events')
