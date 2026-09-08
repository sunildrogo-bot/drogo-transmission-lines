"""production storage, monitoring and query indexes

Revision ID: 20260831_0013
Revises: 20260829_0012
"""
from alembic import op
import sqlalchemy as sa

revision = '20260831_0013'
down_revision = '20260829_0012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_tower_photos_line_tower_id', 'tower_photos', ['line_id', 'tower_label', 'id'])
    op.create_index('ix_tower_defects_photo_active_status', 'tower_defects',
                    ['tower_photo_id', 'deleted_at', 'resolution_status'])
    op.create_index('ix_tower_defects_status_severity', 'tower_defects',
                    ['resolution_status', 'severity'])
    op.create_index('ix_thermal_points_photo_created', 'thermal_points',
                    ['tower_photo_id', 'created_at'])
    op.create_index('ix_tower_status_line_done_label', 'tower_inspection_status',
                    ['line_id', 'inspection_done', 'tower_label'])
    op.create_index('ix_upload_batches_line_status', 'upload_batches', ['line_id', 'status'])
    op.create_table(
        'system_health_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source', sa.String(40), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('response_ms', sa.Float(), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_system_health_source_created', 'system_health_snapshots', ['source', 'created_at'])


def downgrade():
    op.drop_index('ix_system_health_source_created', table_name='system_health_snapshots')
    op.drop_table('system_health_snapshots')
    op.drop_index('ix_upload_batches_line_status', table_name='upload_batches')
    op.drop_index('ix_tower_status_line_done_label', table_name='tower_inspection_status')
    op.drop_index('ix_thermal_points_photo_created', table_name='thermal_points')
    op.drop_index('ix_tower_defects_status_severity', table_name='tower_defects')
    op.drop_index('ix_tower_defects_photo_active_status', table_name='tower_defects')
    op.drop_index('ix_tower_photos_line_tower_id', table_name='tower_photos')
