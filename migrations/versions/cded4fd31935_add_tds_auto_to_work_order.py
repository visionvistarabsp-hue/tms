"""add tds_auto to work_order

Revision ID: cded4fd31935
Revises: a1b2c3d4e5f6
Create Date: 2026-08-18 13:22:43.754673

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'cded4fd31935'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_orders', sa.Column('tds_auto', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('work_orders', 'tds_auto')
