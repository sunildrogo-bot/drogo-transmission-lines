"""Add explicit clean-image review decisions.

Revision ID: 20260825_0007
Revises: 20260825_0006
"""
from alembic import op
import sqlalchemy as sa

revision = '20260825_0007'
down_revision = '20260825_0006'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {row['name'] for row in inspector.get_columns('tower_photos')}
    with op.batch_alter_table('tower_photos') as batch:
        if 'review_outcome' not in columns:
            batch.add_column(sa.Column('review_outcome', sa.String(30), nullable=False, server_default='Pending'))
        if 'reviewed_by_user_id' not in columns:
            batch.add_column(sa.Column('reviewed_by_user_id', sa.Integer(), nullable=True))
            batch.create_foreign_key(
                'fk_tower_photos_reviewed_by_user_id_users',
                'users',
                ['reviewed_by_user_id'],
                ['id'],
                ondelete='SET NULL',
            )
        if 'reviewed_by_name' not in columns:
            batch.add_column(sa.Column('reviewed_by_name', sa.String(120), nullable=True))
        if 'reviewed_at' not in columns:
            batch.add_column(sa.Column('reviewed_at', sa.DateTime(), nullable=True))
def downgrade():
    with op.batch_alter_table('tower_photos') as batch:
        batch.drop_column('reviewed_at')
        batch.drop_column('reviewed_by_name')
        batch.drop_constraint(
            'fk_tower_photos_reviewed_by_user_id_users',
            type_='foreignkey',
        )
        batch.drop_column('reviewed_by_user_id')
        batch.drop_column('review_outcome')
