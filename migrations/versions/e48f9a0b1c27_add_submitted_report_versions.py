"""Add employee-submitted PDF reports and immutable revision history.

Revision ID: e48f9a0b1c27
Revises: d37e8f9a0b16
"""
from alembic import op
import sqlalchemy as sa


revision = "e48f9a0b1c27"
down_revision = "d37e8f9a0b16"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "submitted_report",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
    )
    op.create_index("ix_submitted_report_employee_id", "submitted_report", ["employee_id"])
    op.create_table(
        "submitted_report_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("submitted_report.id"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("total_mandays", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(40), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("pdf_replaced", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("report_id", "revision_number", name="uq_submitted_report_revision"),
        sa.CheckConstraint("revision_number >= 1", name="submitted_report_valid_revision"),
        sa.CheckConstraint("end_date >= start_date", name="submitted_report_date_order"),
        sa.CheckConstraint("total_mandays >= 1", name="submitted_report_positive_mandays"),
    )
    op.create_index("ix_submitted_report_version_report_id", "submitted_report_version", ["report_id"])


def downgrade():
    op.drop_table("submitted_report_version")
    op.drop_table("submitted_report")
