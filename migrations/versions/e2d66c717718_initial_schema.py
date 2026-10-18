"""Initial schema

Revision ID: e2d66c717718
Revises:
Create Date: 2026-10-18 16:14:39.495283
"""
from alembic import op
import sqlalchemy as sa

revision = "e2d66c717718"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("mobile", sa.String(length=40), nullable=False),
        sa.Column("designation", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
    )
    with op.batch_alter_table("user") as batch_op:
        batch_op.create_index(batch_op.f("ix_user_email"), ["email"], unique=True)
    op.create_table(
        "assigned_sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("site_name", sa.String(length=160), nullable=False),
        sa.Column("customer_name", sa.String(length=160), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("allowed_radius", sa.Float(), nullable=False),
        sa.Column("assigned_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["user.id"],
            name=op.f("fk_assigned_sites_employee_id_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assigned_sites")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_audit_log_user_id_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("checkin_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checkout_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkin_lat", sa.Float(), nullable=False),
        sa.Column("checkin_lon", sa.Float(), nullable=False),
        sa.Column("checkout_lat", sa.Float(), nullable=True),
        sa.Column("checkout_lon", sa.Float(), nullable=True),
        sa.Column("checkin_selfie", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["user.id"], name=op.f("fk_attendance_employee_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["assigned_sites.id"],
            name=op.f("fk_attendance_site_id_assigned_sites"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance")),
    )
    op.create_table(
        "site_report",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attendance_id", sa.Integer(), nullable=False),
        sa.Column("work_performed", sa.Text(), nullable=False),
        sa.Column("activities_completed", sa.Text(), nullable=False),
        sa.Column("issues_found", sa.Text(), nullable=False),
        sa.Column("recommendations", sa.Text(), nullable=False),
        sa.Column("materials_used", sa.Text(), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=False),
        sa.Column("engineer_name", sa.String(length=120), nullable=False),
        sa.Column("customer_representative_name", sa.String(length=120), nullable=False),
        sa.Column("employee_signature", sa.String(length=255), nullable=False),
        sa.Column("customer_signature", sa.String(length=255), nullable=False),
        sa.Column("report_date", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["attendance_id"],
            ["attendance.id"],
            name=op.f("fk_site_report_attendance_id_attendance"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_report")),
        sa.UniqueConstraint(
            "attendance_id", name=op.f("uq_site_report_attendance_id")
        ),
    )


def downgrade():
    op.drop_table("site_report")
    op.drop_table("attendance")
    op.drop_table("audit_log")
    op.drop_table("assigned_sites")
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_email"))
    op.drop_table("user")
