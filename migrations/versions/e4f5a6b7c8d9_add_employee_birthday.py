"""Add optional employee birthday.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""
from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("birthday", sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("birthday")
