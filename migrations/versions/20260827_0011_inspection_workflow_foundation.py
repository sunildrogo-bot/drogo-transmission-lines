"""inspection workflow foundation

Revision ID: 20260827_0011
Revises: 20260826_0010
"""
from alembic import op
import sqlalchemy as sa

revision = '20260827_0011'
down_revision = '20260826_0010'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tower_defects') as batch:
        batch.add_column(sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
        batch.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('deleted_by_user_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('deleted_by_name', sa.String(120), nullable=True))
        batch.add_column(sa.Column('deletion_reason', sa.String(255), nullable=True))
        batch.create_foreign_key('fk_tower_defects_deleted_by_user_id_users', 'users', ['deleted_by_user_id'], ['id'], ondelete='SET NULL')

    op.create_table(
        'defect_annotation_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('defect_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('before_json', sa.Text()), sa.Column('after_json', sa.Text()),
        sa.Column('reason', sa.String(255)), sa.Column('changed_by_user_id', sa.Integer()),
        sa.Column('changed_by_name', sa.String(120)), sa.Column('changed_by_role', sa.String(40)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['defect_id'], ['tower_defects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['changed_by_user_id'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_defect_annotation_events_defect_id', 'defect_annotation_events', ['defect_id'])

    op.create_table(
        'inspection_components',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('name', sa.String(150), nullable=False, unique=True),
        sa.Column('description', sa.String(255)), sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('supports_rgb', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('supports_thermal', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('severity_required', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'inspection_defect_types',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('component_id', sa.Integer(), nullable=False), sa.Column('name', sa.String(150), nullable=False),
        sa.Column('report_name', sa.String(150)), sa.Column('training_class', sa.String(150)),
        sa.Column('aliases_json', sa.Text()), sa.Column('severities_json', sa.Text()),
        sa.Column('annotation_method', sa.String(20), nullable=False, server_default='box'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['component_id'], ['inspection_components.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('component_id', 'name', name='uq_inspection_component_defect'),
    )
    op.create_index('ix_inspection_defect_types_component_id', 'inspection_defect_types', ['component_id'])


def downgrade():
    op.drop_index('ix_inspection_defect_types_component_id', table_name='inspection_defect_types')
    op.drop_table('inspection_defect_types')
    op.drop_table('inspection_components')
    op.drop_index('ix_defect_annotation_events_defect_id', table_name='defect_annotation_events')
    op.drop_table('defect_annotation_events')
    with op.batch_alter_table('tower_defects') as batch:
        batch.drop_constraint('fk_tower_defects_deleted_by_user_id_users', type_='foreignkey')
        batch.drop_column('deletion_reason')
        batch.drop_column('deleted_by_name')
        batch.drop_column('deleted_by_user_id')
        batch.drop_column('deleted_at')
        batch.drop_column('version')
