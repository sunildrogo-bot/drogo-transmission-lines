"""Adopt an existing installation or create the application schema.

Revision ID: 20260825_0001
Revises: None
"""
from alembic import op
import sqlalchemy as sa


revision = '20260825_0001'
down_revision = None
branch_labels = None
depends_on = None


def _create_if_missing(table_name, *columns, **kwargs):
    if not sa.inspect(op.get_bind()).has_table(table_name):
        op.create_table(table_name, *columns, **kwargs)


def _create_index_if_missing(index_name, table_name, columns, unique=False):
    inspector = sa.inspect(op.get_bind())
    available_columns = {row['name'] for row in inspector.get_columns(table_name)}
    if not set(columns).issubset(available_columns):
        return
    existing = {row['name'] for row in inspector.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade():
    # Each table is conditional so this revision can safely adopt the database
    # produced by the pre-Alembic application without dropping existing data.
    _create_if_missing(
        'roles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(50), nullable=False, unique=True),
    )
    _create_if_missing(
        'modules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('route', sa.String(100)),
    )
    _create_if_missing(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(120), nullable=False),
        sa.Column('email', sa.String(200), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(256), nullable=False),
        sa.Column('contact', sa.String(30)),
        sa.Column('photo_path', sa.String(255)),
        sa.Column('status', sa.String(20)),
        sa.Column('last_login', sa.String(40)),
        sa.Column('last_login_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('dashboard_notes', sa.Text()),
        sa.Column('reset_token', sa.String(64)),
        sa.Column('reset_token_expires', sa.DateTime()),
    )
    _create_index_if_missing('ix_users_reset_token', 'users', ['reset_token'])
    _create_if_missing(
        'user_roles',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    )
    _create_if_missing(
        'user_modules',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('module_id', sa.Integer(), sa.ForeignKey('modules.id', ondelete='CASCADE'), primary_key=True),
    )
    _create_if_missing(
        'app_settings',
        sa.Column('key', sa.String(80), primary_key=True),
        sa.Column('value', sa.String(255), nullable=False),
    )
    _create_if_missing(
        'activity_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('action', sa.String(40), nullable=False),
        sa.Column('entity_type', sa.String(40), nullable=False),
        sa.Column('entity_name', sa.String(150)),
        sa.Column('module', sa.String(60)),
        sa.Column('performed_by', sa.String(150)),
        sa.Column('role', sa.String(20)),
        sa.Column('duration_seconds', sa.Integer()),
        sa.Column('details', sa.String(255)),
        sa.Column('created_at', sa.DateTime()),
    )
    _create_if_missing(
        'projects',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('module', sa.String(60), nullable=False),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('contact_no', sa.String(30)),
        sa.Column('email', sa.String(200)),
        sa.Column('country', sa.String(100)),
        sa.Column('state', sa.String(100)),
        sa.Column('logo_path', sa.String(255)),
        sa.Column('client_name', sa.String(150)),
        sa.Column('planned_divisions', sa.Integer()),
        sa.Column('planned_towers', sa.Integer()),
        sa.Column('timeline', sa.String(150)),
        sa.Column('legacy_route', sa.String(80)),
        sa.Column('legacy_banner', sa.String(255)),
        sa.Column('inspection_types', sa.Text()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime()),
    )
    _create_if_missing(
        'user_projects',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
    )
    _create_if_missing(
        'divisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('client_name', sa.String(150)),
        sa.Column('state', sa.String(100)),
        sa.Column('planned_towers', sa.Integer()),
        sa.Column('created_at', sa.DateTime()),
    )
    _create_if_missing(
        'lines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('division_id', sa.Integer(), sa.ForeignKey('divisions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('start_lat', sa.Float(), nullable=False),
        sa.Column('start_lng', sa.Float(), nullable=False),
        sa.Column('end_lat', sa.Float(), nullable=False),
        sa.Column('end_lng', sa.Float(), nullable=False),
        sa.Column('length_km', sa.Float()),
        sa.Column('tower_count', sa.Integer()),
        sa.Column('kml_path', sa.String(255)),
        sa.Column('voltage_level', sa.String(50)),
        sa.Column('survey_date', sa.Date()),
        sa.Column('pilot_name', sa.String(150)),
        sa.Column('inspection_name', sa.String(150)),
        sa.Column('visible_kml_attrs', sa.Text()),
        sa.Column('created_at', sa.DateTime()),
    )
    _create_if_missing(
        'corridor_photos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('line_id', sa.Integer(), sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('image_path', sa.String(255), nullable=False),
        sa.Column('gps_lat', sa.Float(), nullable=False),
        sa.Column('gps_lng', sa.Float(), nullable=False),
        sa.Column('observation', sa.Text()),
        sa.Column('uploaded_by', sa.String(120)),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('thumbnail_path', sa.String(255)),
    )
    _create_if_missing(
        'tower_photos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('line_id', sa.Integer(), sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tower_label', sa.String(150), nullable=False),
        sa.Column('image_path', sa.String(255), nullable=False),
        sa.Column('uploaded_by', sa.String(120)),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('gps_lat', sa.Float()),
        sa.Column('gps_lng', sa.Float()),
        sa.Column('content_hash', sa.String(64)),
        sa.Column('defect_copy_path', sa.String(255)),
        sa.Column('raw_deleted', sa.Boolean()),
        sa.Column('thumbnail_path', sa.String(255)),
    )
    _create_index_if_missing('ix_tower_photos_content_hash', 'tower_photos', ['content_hash'])
    _create_if_missing(
        'sme_assignments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('line_id', sa.Integer(), sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sme_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('assigned_by', sa.String(120)),
        sa.Column('assigned_at', sa.DateTime()),
        sa.Column('seen_by_sme', sa.Boolean()),
        sa.UniqueConstraint('line_id', 'sme_user_id', name='uq_sme_line'),
    )
    _create_if_missing(
        'pilot_locations',
        sa.Column('pilot_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lng', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.DateTime()),
    )
    _create_if_missing(
        'pilot_assignments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('line_id', sa.Integer(), sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('pilot_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('assigned_by', sa.String(120)),
        sa.Column('assigned_at', sa.DateTime()),
        sa.Column('seen_by_pilot', sa.Boolean()),
        sa.UniqueConstraint('line_id', 'pilot_user_id', name='uq_pilot_line'),
    )
    _create_if_missing(
        'tower_inspection_status',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('line_id', sa.Integer(), sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tower_label', sa.String(150), nullable=False),
        sa.Column('inspection_done', sa.Boolean()),
        sa.Column('marked_by', sa.String(120)),
        sa.Column('marked_at', sa.DateTime()),
        sa.Column('zone', sa.String(10)),
        sa.Column('zone_set_by', sa.String(120)),
        sa.Column('zone_set_at', sa.DateTime()),
        sa.Column('pilot_submitted', sa.Boolean()),
        sa.Column('pilot_submitted_by', sa.String(120)),
        sa.Column('pilot_submitted_at', sa.DateTime()),
        sa.UniqueConstraint('line_id', 'tower_label', name='uq_tower_inspection'),
    )
    _create_if_missing(
        'tower_defects',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tower_photo_id', sa.Integer(), sa.ForeignKey('tower_photos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('shape_type', sa.String(20), nullable=False),
        sa.Column('shape_coords', sa.Text(), nullable=False),
        sa.Column('component_name', sa.String(150)),
        sa.Column('location', sa.String(20)),
        sa.Column('defect_type', sa.String(100)),
        sa.Column('observation', sa.String(255)),
        sa.Column('severity', sa.String(20)),
        sa.Column('status', sa.String(20)),
        sa.Column('resolution_status', sa.String(20)),
        sa.Column('resolved_by', sa.String(120)),
        sa.Column('resolved_at', sa.DateTime()),
        sa.Column('resolution_comment', sa.Text()),
        sa.Column('comments', sa.Text()),
        sa.Column('created_by', sa.String(120)),
        sa.Column('created_at', sa.DateTime()),
    )
    _create_if_missing(
        'thermal_points',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tower_photo_id', sa.Integer(), sa.ForeignKey('tower_photos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('x_pct', sa.Float(), nullable=False),
        sa.Column('y_pct', sa.Float(), nullable=False),
        sa.Column('shape_type', sa.String(10)),
        sa.Column('shape_coords', sa.Text()),
        sa.Column('temperature_c', sa.Float()),
        sa.Column('min_c', sa.Float()),
        sa.Column('max_c', sa.Float()),
        sa.Column('avg_c', sa.Float()),
        sa.Column('raw_avg', sa.Float()),
        sa.Column('raw_min', sa.Float()),
        sa.Column('raw_max', sa.Float()),
        sa.Column('label', sa.String(100)),
        sa.Column('error', sa.String(255)),
        sa.Column('created_by', sa.String(120)),
        sa.Column('created_at', sa.DateTime()),
    )
    _create_if_missing(
        'tower_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('line_id', sa.Integer(), sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tower_label', sa.String(150), nullable=False),
        sa.Column('report_path', sa.String(255), nullable=False),
        sa.Column('generated_by', sa.String(120)),
        sa.Column('generated_at', sa.DateTime()),
    )
    _create_if_missing(
        'announcements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('title', sa.String(150), nullable=False),
        sa.Column('message', sa.Text()),
        sa.Column('image_path', sa.String(255)),
        sa.Column('created_by', sa.String(120)),
        sa.Column('created_at', sa.DateTime()),
    )
    _create_if_missing(
        'help_tickets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('subject', sa.String(150), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('reporter_type', sa.String(20)),
        sa.Column('submitted_by', sa.String(120)),
        sa.Column('status', sa.String(20)),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('updated_at', sa.DateTime()),
        sa.Column('resolved_by', sa.String(120)),
        sa.Column('seen_by_reporter', sa.Boolean()),
    )
    _create_if_missing(
        'contact_inquiries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('email', sa.String(150), nullable=False),
        sa.Column('company', sa.String(150)),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20)),
        sa.Column('created_at', sa.DateTime()),
    )


def downgrade():
    # This revision may have adopted tables that predate Alembic. A downgrade
    # must never guess which tables it owns and accidentally destroy user data.
    pass
