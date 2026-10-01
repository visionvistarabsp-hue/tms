"""add transporter_id to work_order

Revision ID: 38b3abcf9bea
Revises: cded4fd31935
Create Date: 2026-08-18 13:44:20.199452

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '38b3abcf9bea'
down_revision = 'cded4fd31935'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('work_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('transporter_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_work_orders_transporter_id'), ['transporter_id'], unique=False)
        batch_op.create_foreign_key(None, 'transporters', ['transporter_id'], ['id'])


def downgrade():
    with op.batch_alter_table('work_orders', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_work_orders_transporter_id'))
        batch_op.drop_column('transporter_id')
