"""Add daily non-site approvals and effective-date attendance policy.

Revision ID: f59a0b1c2d38
Revises: e48f9a0b1c27
"""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa


revision = "f59a0b1c2d38"
down_revision = "e48f9a0b1c27"
branch_labels = None
depends_on = None


def upgrade():
    policy = op.create_table(
        "attendance_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
    )
    op.bulk_insert(policy, [{
        "id": 1,
        "effective_from": datetime.now(timezone(timedelta(hours=5, minutes=30))).date(),
    }])
    op.create_table(
        "daily_work_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("work_type", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("user.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("employee_id", "work_date", name="uq_daily_work_employee_date"),
        sa.CheckConstraint("work_type IN ('wfh', 'travelling', 'training')", name="daily_work_valid_type"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="daily_work_valid_status"),
    )
    op.create_index("ix_daily_work_status_employee_id", "daily_work_status", ["employee_id"])
    op.create_index("ix_daily_work_status_work_date", "daily_work_status", ["work_date"])
    with op.batch_alter_table("attendance") as batch:
        batch.add_column(sa.Column("site_radius_m", sa.Float()))


def downgrade():
    with op.batch_alter_table("attendance") as batch:
        batch.drop_column("site_radius_m")
    op.drop_table("daily_work_status")
    op.drop_table("attendance_policy")
