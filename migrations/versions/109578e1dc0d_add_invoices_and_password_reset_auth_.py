"""Add invoices and password reset auth version

Revision ID: 109578e1dc0d
Revises: e2d66c717718
Create Date: 2026-10-18 20:57:40.761122
"""
from alembic import op
import sqlalchemy as sa

revision = "109578e1dc0d"
down_revision = "e2d66c717718"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoice",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(length=40), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("customer_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("billable_days", sa.Integer(), nullable=False),
        sa.Column("daily_rate", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["user.id"], name=op.f("fk_invoice_created_by_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["user.id"], name=op.f("fk_invoice_employee_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["assigned_sites.id"],
            name=op.f("fk_invoice_site_id_assigned_sites"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice")),
    )
    with op.batch_alter_table("invoice") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_invoice_invoice_number"),
            ["invoice_number"],
            unique=True,
        )
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(
            sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("auth_version")
    with op.batch_alter_table("invoice") as batch_op:
        batch_op.drop_index(batch_op.f("ix_invoice_invoice_number"))
    op.drop_table("invoice")
