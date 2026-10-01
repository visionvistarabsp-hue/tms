"""add tds_rate to transporter

Revision ID: a1b2c3d4e5f6
Revises: b163f2c35a7d
Create Date: 2026-08-18 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'b163f2c35a7d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('transporters', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tds_rate', sa.Numeric(precision=5, scale=2), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('transporters', schema=None) as batch_op:
        batch_op.drop_column('tds_rate')
