"""Add admin-managed email settings and Android download switch.

Revision ID: b15c6d7e8f94
Revises: a04b5c6d7e83
"""
from alembic import op
import sqlalchemy as sa

revision = "b15c6d7e8f94"
down_revision = "a04b5c6d7e83"
branch_labels = None
depends_on = None

COLUMNS = (
    ("public_base_url", sa.String(length=500), ""),
    ("mail_server", sa.String(length=255), ""),
    ("mail_port", sa.Integer(), "465"),
    ("mail_security", sa.String(length=10), "ssl"),
    ("mail_username", sa.String(length=255), ""),
    ("mail_password", sa.String(length=500), ""),
    ("mail_sender", sa.String(length=255), ""),
)


def upgrade():
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(sa.Column(
            "android_download_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ))
        for name, column_type, default in COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=False, server_default=default))
    op.execute(
        "UPDATE app_settings SET android_download_enabled = "
        + ("true" if op.get_bind().dialect.name == "postgresql" else "1")
        + " WHERE android_server_url <> ''"
    )


def downgrade():
    with op.batch_alter_table("app_settings") as batch:
        for name, _, _ in reversed(COLUMNS):
            batch.drop_column(name)
        batch.drop_column("android_download_enabled")
