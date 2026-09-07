"""release compatibility, shared monitoring and worker reliability

Revision ID: 20260901_0014
Revises: 20260831_0013
"""
from alembic import op
import sqlalchemy as sa

revision = '20260901_0014'
down_revision = '20260831_0013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'service_heartbeats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('service_id', sa.String(180), nullable=False),
        sa.Column('service_type', sa.String(30), nullable=False),
        sa.Column('hostname', sa.String(180), nullable=False, server_default=''),
        sa.Column('process_id', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='Running'),
        sa.Column('current_job_id', sa.Integer(), sa.ForeignKey('background_jobs.id', ondelete='SET NULL')),
        sa.Column('details_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_service_heartbeats_service_id', 'service_heartbeats', ['service_id'], unique=True)
    op.create_index('ix_service_heartbeats_service_type', 'service_heartbeats', ['service_type'])
    op.create_index('ix_service_heartbeats_last_seen', 'service_heartbeats', ['last_seen'])
    op.create_table(
        'request_metric_buckets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bucket_start', sa.DateTime(), nullable=False),
        sa.Column('source_id', sa.String(180), nullable=False),
        sa.Column('request_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_response_ms', sa.Float(), nullable=False, server_default='0'),
        sa.Column('max_response_ms', sa.Float(), nullable=False, server_default='0'),
        sa.Column('active_users_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('bucket_start', 'source_id', name='uq_request_metric_bucket_source'),
    )
    op.create_index('ix_request_metric_bucket_start', 'request_metric_buckets', ['bucket_start'])
    op.create_table(
        'application_releases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('version', sa.String(40), nullable=False),
        sa.Column('migration_revision', sa.String(40), nullable=False, server_default='unknown'),
        sa.Column('status', sa.String(20), nullable=False, server_default='Verified'),
        sa.Column('details_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('installed_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_application_releases_version', 'application_releases', ['version'])


def downgrade():
    op.drop_index('ix_application_releases_version', table_name='application_releases')
    op.drop_table('application_releases')
    op.drop_index('ix_request_metric_bucket_start', table_name='request_metric_buckets')
    op.drop_table('request_metric_buckets')
    op.drop_index('ix_service_heartbeats_last_seen', table_name='service_heartbeats')
    op.drop_index('ix_service_heartbeats_service_type', table_name='service_heartbeats')
    op.drop_index('ix_service_heartbeats_service_id', table_name='service_heartbeats')
    op.drop_table('service_heartbeats')
