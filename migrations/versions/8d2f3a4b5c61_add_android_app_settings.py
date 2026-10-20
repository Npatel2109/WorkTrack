"""Add Android app settings.

Revision ID: 8d2f3a4b5c61
Revises: 7c1e2d3f4a50
"""
from alembic import op
import sqlalchemy as sa

revision = "8d2f3a4b5c61"
down_revision = "7c1e2d3f4a50"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("android_server_url", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("app_settings")
