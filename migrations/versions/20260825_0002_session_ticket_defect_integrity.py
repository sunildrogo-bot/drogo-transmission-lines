"""Add session revocation, ticket ownership, and defect workflow integrity.

Revision ID: 20260825_0002
Revises: 20260825_0001
"""
from alembic import op
import sqlalchemy as sa


revision = '20260825_0002'
down_revision = '20260825_0001'
branch_labels = None
depends_on = None


def _columns(table_name):
    return {row['name'] for row in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name):
    return {row['name'] for row in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _foreign_keys(table_name):
    return sa.inspect(op.get_bind()).get_foreign_keys(table_name)


def _has_ticket_owner_foreign_key():
    return any(
        row.get('referred_table') == 'users'
        and row.get('constrained_columns') == ['submitted_by_user_id']
        for row in _foreign_keys('help_tickets')
    )


def upgrade():
    bind = op.get_bind()

    # Bug 9: a database-backed generation counter revokes stale cookies after
    # an account, password, role, module, project assignment, or status change.
    if 'session_version' not in _columns('users'):
        op.add_column(
            'users',
            sa.Column('session_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        )
    else:
        bind.execute(sa.text(
            "UPDATE users SET session_version = 1 WHERE session_version IS NULL"
        ))
        with op.batch_alter_table('users') as batch_op:
            batch_op.alter_column(
                'session_version', existing_type=sa.Integer(), nullable=False,
                server_default=sa.text('1'),
            )

    # Bug 10: ownership is an immutable user ID, not a mutable/non-unique name.
    if 'submitted_by_user_id' not in _columns('help_tickets'):
        with op.batch_alter_table('help_tickets') as batch_op:
            batch_op.add_column(sa.Column('submitted_by_user_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                'fk_help_tickets_submitted_by_user_id_users',
                'users', ['submitted_by_user_id'], ['id'], ondelete='SET NULL',
            )
    elif not _has_ticket_owner_foreign_key():
        with op.batch_alter_table('help_tickets') as batch_op:
            batch_op.create_foreign_key(
                'fk_help_tickets_submitted_by_user_id_users',
                'users', ['submitted_by_user_id'], ['id'], ondelete='SET NULL',
            )

    if 'ix_help_tickets_submitted_by_user_id' not in _indexes('help_tickets'):
        op.create_index(
            'ix_help_tickets_submitted_by_user_id',
            'help_tickets', ['submitted_by_user_id'], unique=False,
        )

    # Backfill only when a legacy display name identifies exactly one account.
    # Ambiguous duplicate names and unmatched tickets remain Admin-only rather
    # than guessing ownership and risking disclosure to the wrong person.
    bind.execute(sa.text(
        """
        UPDATE help_tickets
        SET submitted_by_user_id = (
            SELECT MIN(users.id)
            FROM users
            WHERE LOWER(users.username) = LOWER(help_tickets.submitted_by)
        )
        WHERE submitted_by_user_id IS NULL
          AND submitted_by IS NOT NULL
          AND submitted_by <> ''
          AND (
              SELECT COUNT(users.id)
              FROM users
              WHERE LOWER(users.username) = LOWER(help_tickets.submitted_by)
          ) = 1
        """
    ))

    # Bug 11: status describes component condition (OK/Missing), while
    # resolution_status is the defect workflow (Open/Closed).
    if 'resolution_status' not in _columns('tower_defects'):
        op.add_column(
            'tower_defects',
            sa.Column(
                'resolution_status', sa.String(20), nullable=False,
                server_default=sa.text("'Open'"),
            ),
        )
    else:
        bind.execute(sa.text(
            "UPDATE tower_defects SET resolution_status = 'Open' "
            "WHERE resolution_status IS NULL OR resolution_status = ''"
        ))
        with op.batch_alter_table('tower_defects') as batch_op:
            batch_op.alter_column(
                'resolution_status', existing_type=sa.String(20), nullable=False,
                server_default=sa.text("'Open'"),
            )

    # Repair values written into the wrong column by the deprecated startup
    # migration. "Open" is not a valid component condition.
    if 'status' in _columns('tower_defects'):
        bind.execute(sa.text(
            "UPDATE tower_defects SET status = 'OK' WHERE status = 'Open'"
        ))


def downgrade():
    if 'ix_help_tickets_submitted_by_user_id' in _indexes('help_tickets'):
        op.drop_index('ix_help_tickets_submitted_by_user_id', table_name='help_tickets')
    if 'submitted_by_user_id' in _columns('help_tickets'):
        with op.batch_alter_table('help_tickets') as batch_op:
            fk_names = {row.get('name') for row in _foreign_keys('help_tickets')}
            if 'fk_help_tickets_submitted_by_user_id_users' in fk_names:
                batch_op.drop_constraint(
                    'fk_help_tickets_submitted_by_user_id_users', type_='foreignkey'
                )
            batch_op.drop_column('submitted_by_user_id')
    if 'session_version' in _columns('users'):
        with op.batch_alter_table('users') as batch_op:
            batch_op.drop_column('session_version')

    # resolution_status existed before this revision. Keep it and the corrected
    # data; a downgrade must not reintroduce the status-column corruption.
