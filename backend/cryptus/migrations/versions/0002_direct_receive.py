"""direct receiving: one merchant wallet, a unique memo per deposit

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('deposit_addresses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('memo', sa.String(length=64), nullable=False, server_default=''))
        batch_op.drop_constraint('uq_deposit_address', type_='unique')
        batch_op.create_unique_constraint('uq_deposit_address', ['network', 'address', 'memo'])


def downgrade() -> None:
    with op.batch_alter_table('deposit_addresses', schema=None) as batch_op:
        batch_op.drop_constraint('uq_deposit_address', type_='unique')
        batch_op.create_unique_constraint('uq_deposit_address', ['network', 'address'])
        batch_op.drop_column('memo')
