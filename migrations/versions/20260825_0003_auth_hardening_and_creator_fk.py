"""Add authentication throttling and safe project creator deletion.

Revision ID: 20260825_0003
Revises: 20260825_0002
"""
from alembic import op
import sqlalchemy as sa


revision = '20260825_0003'
down_revision = '20260825_0002'
branch_labels = None
depends_on = None


FK_NAMING = {
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
}


def _has_table(table_name):
    return sa.inspect(op.get_bind()).has_table(table_name)


def _indexes(table_name):
    if not _has_table(table_name):
        return set()
    return {row['name'] for row in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _project_creator_fk():
    if not _has_table('projects'):
        return None
    for row in sa.inspect(op.get_bind()).get_foreign_keys('projects'):
        if (
            row.get('referred_table') == 'users'
            and row.get('constrained_columns') == ['created_by']
        ):
            return row
    return None


def _fk_uses_set_null(row):
    options = (row or {}).get('options') or {}
    return str(options.get('ondelete') or '').upper().replace('_', ' ') == 'SET NULL'


def _replace_project_creator_fk(ondelete=None):
    existing = _project_creator_fk()
    if existing and str((existing.get('options') or {}).get('ondelete') or '').upper() == str(ondelete or '').upper():
        return

    with op.batch_alter_table('projects', naming_convention=FK_NAMING) as batch_op:
        if existing:
            # SQLite commonly stores this legacy constraint without a name.
            # The naming convention gives reflected unnamed FKs this stable
            # name while the batch operation recreates the table.
            constraint_name = existing.get('name') or 'fk_projects_created_by_users'
            batch_op.drop_constraint(constraint_name, type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_projects_created_by_users',
            'users', ['created_by'], ['id'], ondelete=ondelete,
        )


def upgrade():
    # Bug 8: a shared database table keeps login and reset limits effective
    # across Flask workers without storing raw email addresses or IPs.
    if not _has_table('auth_rate_limits'):
        op.create_table(
            'auth_rate_limits',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('scope', sa.String(40), nullable=False),
            sa.Column('key_hash', sa.String(64), nullable=False),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('window_started_at', sa.DateTime(), nullable=False),
            sa.Column('blocked_until', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('scope', 'key_hash', name='uq_auth_rate_limit_scope_key'),
        )
    if 'ix_auth_rate_limits_updated_at' not in _indexes('auth_rate_limits'):
        op.create_index(
            'ix_auth_rate_limits_updated_at',
            'auth_rate_limits', ['updated_at'], unique=False,
        )

    # Bug 16: deleting a creator must preserve their projects. The creator is
    # historical metadata, so the database clears it instead of rejecting the
    # user deletion or cascading into project data.
    creator_fk = _project_creator_fk()
    if not _fk_uses_set_null(creator_fk):
        _replace_project_creator_fk(ondelete='SET NULL')


def downgrade():
    # Keep projects, but restore the legacy no-action creator reference.
    creator_fk = _project_creator_fk()
    if _fk_uses_set_null(creator_fk):
        _replace_project_creator_fk(ondelete=None)

    if _has_table('auth_rate_limits'):
        op.drop_table('auth_rate_limits')
