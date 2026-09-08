"""upload validation, persistent jobs and cached map foundation

Revision ID: 20260829_0012
Revises: 20260827_0011
"""
from alembic import op
import sqlalchemy as sa

revision = '20260829_0012'
down_revision = '20260827_0011'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('lines') as batch:
        batch.add_column(sa.Column('geojson_path', sa.String(255), nullable=True))
    with op.batch_alter_table('tower_photos') as batch:
        batch.add_column(sa.Column('media_type', sa.String(20), nullable=True))
        batch.add_column(sa.Column('image_width', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('image_height', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('captured_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('validation_status', sa.String(20), nullable=True))
        batch.add_column(sa.Column('validation_warnings_json', sa.Text(), nullable=True))
        batch.create_index('ix_tower_photos_media_type', ['media_type'])
    op.create_table(
        'background_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_type', sa.String(60), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='Queued'),
        sa.Column('payload_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('progress_current', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('progress_total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('result_json', sa.Text()), sa.Column('error_message', sa.Text()),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('created_by_user_id', sa.Integer()), sa.Column('created_by_name', sa.String(120)),
        sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('started_at', sa.DateTime()),
        sa.Column('finished_at', sa.DateTime()), sa.Column('heartbeat_at', sa.DateTime()),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_background_jobs_job_type', 'background_jobs', ['job_type'])
    op.create_index('ix_background_jobs_status', 'background_jobs', ['status'])
    op.create_index('ix_background_jobs_created_at', 'background_jobs', ['created_at'])


def downgrade():
    op.drop_index('ix_background_jobs_created_at', table_name='background_jobs')
    op.drop_index('ix_background_jobs_status', table_name='background_jobs')
    op.drop_index('ix_background_jobs_job_type', table_name='background_jobs')
    op.drop_table('background_jobs')
    with op.batch_alter_table('tower_photos') as batch:
        batch.drop_index('ix_tower_photos_media_type')
        for name in ('validation_warnings_json', 'validation_status', 'captured_at', 'image_height', 'image_width', 'media_type'):
            batch.drop_column(name)
    with op.batch_alter_table('lines') as batch:
        batch.drop_column('geojson_path')
