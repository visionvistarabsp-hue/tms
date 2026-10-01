"""drop rtgs from work_order

Revision ID: 9f8e7d6c5b4a
Revises: 2a3b4c5d6e7f
Create Date: 2026-09-09

"""
from alembic import op

revision = '9f8e7d6c5b4a'
down_revision = '2a3b4c5d6e7f'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column('work_orders', 'rtgs')


def downgrade():
    import sqlalchemy as sa
    op.add_column('work_orders', sa.Column('rtgs', sa.Numeric(12, 2), nullable=False, server_default='0'))