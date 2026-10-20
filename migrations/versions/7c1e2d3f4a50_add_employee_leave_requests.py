"""Add employee leave requests.

Revision ID: 7c1e2d3f4a50
Revises: 109578e1dc0d
"""
from alembic import op
import sqlalchemy as sa

revision = "7c1e2d3f4a50"
down_revision = "109578e1dc0d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "leave_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("end_date >= start_date", name="leave_date_order"),
        sa.CheckConstraint("period IN ('full', 'morning', 'afternoon')", name="leave_valid_period"),
        sa.CheckConstraint("period = 'full' OR start_date = end_date", name="leave_half_day_date"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="leave_valid_status"),
        sa.ForeignKeyConstraint(["employee_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_leave_request_employee_id", "leave_request", ["employee_id"])
    op.create_index("ix_leave_request_status", "leave_request", ["status"])


def downgrade():
    op.drop_index("ix_leave_request_status", table_name="leave_request")
    op.drop_index("ix_leave_request_employee_id", table_name="leave_request")
    op.drop_table("leave_request")
