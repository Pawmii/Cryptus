"""sweeps: room for the longest status and for idempotency keys with a retry suffix

"awaiting_external_signature" is 27 characters; PostgreSQL rejected it in a 24-character column.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('sweeps', schema=None) as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(length=24), type_=sa.String(length=32),
                              existing_nullable=False)
        batch_op.alter_column('idempotency_key', existing_type=sa.String(length=128), type_=sa.String(length=200),
                              existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('sweeps', schema=None) as batch_op:
        batch_op.alter_column('idempotency_key', existing_type=sa.String(length=200), type_=sa.String(length=128),
                              existing_nullable=False)
        batch_op.alter_column('status', existing_type=sa.String(length=32), type_=sa.String(length=24),
                              existing_nullable=False)
