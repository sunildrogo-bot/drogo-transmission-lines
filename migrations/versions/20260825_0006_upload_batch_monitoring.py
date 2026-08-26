"""Add persistent Admin bulk-upload monitoring.

Revision ID: 20260825_0006
Revises: 20260825_0005
"""
from alembic import op
import sqlalchemy as sa

revision = '20260825_0006'
down_revision = '20260825_0005'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('upload_batches'):
        op.create_table('upload_batches',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('line_id', sa.Integer(), sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
            sa.Column('uploaded_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
            sa.Column('uploaded_by_name', sa.String(120)), sa.Column('folder_name', sa.String(255)),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('total_files', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('matched_files', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('completed_files', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('duplicate_files', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('failed_files', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('no_gps_files', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('unmatched_files', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cancelled_files', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('started_at', sa.DateTime(), nullable=False), sa.Column('finished_at', sa.DateTime()))
        op.create_index('ix_upload_batches_line_id', 'upload_batches', ['line_id'])
    if not inspector.has_table('upload_batch_items'):
        op.create_table('upload_batch_items',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('batch_id', sa.Integer(), sa.ForeignKey('upload_batches.id', ondelete='CASCADE'), nullable=False),
            sa.Column('filename', sa.String(500), nullable=False), sa.Column('tower_label', sa.String(150)),
            sa.Column('status', sa.String(20), nullable=False), sa.Column('error_message', sa.Text()),
            sa.Column('file_size', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=False))
        op.create_index('ix_upload_batch_items_batch_id', 'upload_batch_items', ['batch_id'])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table('upload_batch_items'):
        op.drop_table('upload_batch_items')
    if inspector.has_table('upload_batches'):
        op.drop_table('upload_batches')
