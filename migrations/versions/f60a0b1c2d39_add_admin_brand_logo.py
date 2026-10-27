"""Add an admin-configurable company logo.

Revision ID: f60a0b1c2d39
Revises: e48f9a0b1c27
"""
from alembic import op
import sqlalchemy as sa


revision = "f60a0b1c2d39"
down_revision = "e48f9a0b1c27"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(sa.Column("brand_logo_filename", sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("brand_logo_filename")
