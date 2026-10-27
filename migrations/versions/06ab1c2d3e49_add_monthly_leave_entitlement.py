"""Add per-employee monthly leave entitlement.

Revision ID: 06ab1c2d3e49
Revises: f59a0b1c2d38
"""
from alembic import op
import sqlalchemy as sa


revision = "06ab1c2d3e49"
down_revision = "f59a0b1c2d38"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "monthly_leave_entitlement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("days", sa.Numeric(6, 1), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("employee_id", "month", name="uq_leave_entitlement_employee_month"),
        sa.CheckConstraint("days >= 0 AND days <= 31", name="leave_entitlement_valid_days"),
    )
    op.create_index("ix_monthly_leave_entitlement_employee_id", "monthly_leave_entitlement", ["employee_id"])


def downgrade():
    op.drop_table("monthly_leave_entitlement")
