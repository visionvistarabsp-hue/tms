"""widen shortage precision to 4 decimal places

Revision ID: 5f2c8d1e3a6b
Revises: 4e1a7c90b2d5
Create Date: 2026-09-23

"""
from alembic import op
import sqlalchemy as sa

revision = '5f2c8d1e3a6b'
down_revision = '4e1a7c90b2d5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.alter_column(
            'shortage',
            existing_type=sa.Numeric(12, 2),
            type_=sa.Numeric(14, 4),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.alter_column(
            'shortage',
            existing_type=sa.Numeric(14, 4),
            type_=sa.Numeric(12, 2),
            existing_nullable=False,
        )