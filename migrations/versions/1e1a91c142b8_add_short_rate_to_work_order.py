"""add short_rate to work_order

Revision ID: 1e1a91c142b8
Revises: ccf7f59eeba2
Create Date: 2026-08-18

"""
from alembic import op
import sqlalchemy as sa

revision = '1e1a91c142b8'
down_revision = 'ccf7f59eeba2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_orders', sa.Column('short_rate', sa.Numeric(12, 2), nullable=False, server_default='10000'))


def downgrade():
    op.drop_column('work_orders', 'short_rate')
