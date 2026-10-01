"""add payable_date to work_order

Revision ID: 2a3b4c5d6e7f
Revises: 1e1a91c142b8
Create Date: 2026-09-08

"""
from alembic import op
import sqlalchemy as sa

revision = '2a3b4c5d6e7f'
down_revision = '1e1a91c142b8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_orders', sa.Column('payable_date', sa.Date(), nullable=True))


def downgrade():
    op.drop_column('work_orders', 'payable_date')