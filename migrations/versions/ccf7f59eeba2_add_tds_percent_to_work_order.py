"""add tds_percent to work_order

Revision ID: ccf7f59eeba2
Revises: 38b3abcf9bea
Create Date: 2026-08-18 15:11:51.709776

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'ccf7f59eeba2'
down_revision = '38b3abcf9bea'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_orders', sa.Column('tds_percent', sa.Numeric(5, 2), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('work_orders', 'tds_percent')
