"""Add candidate referrals and PO man-day tracking.

Revision ID: b1c2d3e4f5a6
Revises: 7a2b3c4d5e6f
"""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "7a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("referral_notification_email", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("po_notification_email", sa.String(length=255), nullable=False, server_default=""))

    op.create_table(
        "candidate_referral",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("candidate_name", sa.String(length=120), nullable=False),
        sa.Column("candidate_email", sa.String(length=255), nullable=False),
        sa.Column("candidate_mobile", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("position", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="submitted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("candidate_referral") as batch_op:
        batch_op.create_index("ix_candidate_referral_employee_id", ["employee_id"])

    op.create_table(
        "purchase_order",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("po_number", sa.String(length=120), nullable=False),
        sa.Column("customer_name", sa.String(length=160), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("assigned_sites.id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("total_mandays", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_mandays", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invoice_generated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("money_received", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("completion_email_sent", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("purchase_order") as batch_op:
        batch_op.create_index("ix_purchase_order_employee_id", ["employee_id"])
        batch_op.create_index("ix_purchase_order_site_id", ["site_id"])
        batch_op.create_index("ix_purchase_order_po_number", ["po_number"], unique=True)


def downgrade():
    op.drop_table("purchase_order")
    op.drop_table("candidate_referral")
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("po_notification_email")
        batch_op.drop_column("referral_notification_email")
