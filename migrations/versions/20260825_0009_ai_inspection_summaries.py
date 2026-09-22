"""Add source-backed AI inspection summaries.

Revision ID: 20260825_0009
Revises: 20260825_0008
"""
from alembic import op
import sqlalchemy as sa

revision = '20260825_0009'
down_revision = '20260825_0008'
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('ai_inspection_summaries'):
        op.create_table('ai_inspection_summaries',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('line_id', sa.Integer(), sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
            sa.Column('tower_label', sa.String(150), nullable=False),
            sa.Column('findings_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('sources_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('is_shared', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('generated_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
            sa.Column('generated_by_name', sa.String(120)),
            sa.Column('generated_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('line_id', 'tower_label', name='uq_ai_summary_tower'))
        op.create_index('ix_ai_inspection_summaries_line_id', 'ai_inspection_summaries', ['line_id'])


def downgrade():
    if sa.inspect(op.get_bind()).has_table('ai_inspection_summaries'):
        op.drop_table('ai_inspection_summaries')
