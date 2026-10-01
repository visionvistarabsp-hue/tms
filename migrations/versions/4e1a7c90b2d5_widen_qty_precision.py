"""widen qty precision to 4 decimal places for work orders

Revision ID: 4e1a7c90b2d5
Revises: 9f8e7d6c5b4a
Create Date: 2026-09-23

"""
from alembic import op
import sqlalchemy as sa

revision = '4e1a7c90b2d5'
down_revision = '9f8e7d6c5b4a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.alter_column(
            'mines_qty',
            existing_type=sa.Numeric(12, 2),
            type_=sa.Numeric(14, 4),
            existing_nullable=True,
        )
        batch_op.alter_column(
            'plant_qty',
            existing_type=sa.Numeric(12, 2),
            type_=sa.Numeric(14, 4),
            existing_nullable=True,
        )


def downgrade():
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.alter_column(
            'mines_qty',
            existing_type=sa.Numeric(14, 4),
            type_=sa.Numeric(12, 2),
            existing_nullable=True,
        )
        batch_op.alter_column(
            'plant_qty',
            existing_type=sa.Numeric(14, 4),
            type_=sa.Numeric(12, 2),
            existing_nullable=True,
        )