"""Add joining date and opening balance for cumulative leave allowances.

Revision ID: 17bc2d3e4f50
Revises: 06ab1c2d3e49
"""
from alembic import op
import sqlalchemy as sa


revision = "17bc2d3e4f50"
down_revision = "06ab1c2d3e49"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "leave_allowance_account",
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("joining_date", sa.Date(), nullable=False),
        sa.Column("opening_days", sa.Numeric(8, 1), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("opening_days >= 0", name="leave_opening_nonnegative"),
    )


def downgrade():
    op.drop_table("leave_allowance_account")
