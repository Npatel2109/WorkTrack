"""Add optional admin-managed employee details.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""
from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("address", sa.String(1000), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("emergency_contact_no", sa.String(40), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("blood_group", sa.String(3), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("laptop_serial_no", sa.String(120), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("employee_code", sa.String(80), nullable=True))
        batch_op.create_unique_constraint("uq_user_employee_code", ["employee_code"])


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_constraint("uq_user_employee_code", type_="unique")
        for column in ("employee_code", "laptop_serial_no", "blood_group", "emergency_contact_no", "address"):
            batch_op.drop_column(column)
