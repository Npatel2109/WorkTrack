"""Separate reusable sites from engineer assignments and add invoice PO fields.

Revision ID: d37e8f9a0b16
Revises: c26d7e8f9a05
"""
from alembic import op
import sqlalchemy as sa


revision = "d37e8f9a0b16"
down_revision = "c26d7e8f9a05"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "site_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("assigned_sites.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("employee_id", "site_id", name="uq_employee_site_assignment"),
    )
    op.create_index("ix_site_assignment_employee_id", "site_assignment", ["employee_id"])
    op.create_index("ix_site_assignment_site_id", "site_assignment", ["site_id"])
    op.execute("""
        INSERT INTO site_assignment (employee_id, site_id, is_active, assigned_at)
        SELECT employee_id, id, is_active, assigned_date FROM assigned_sites
    """)
    # Retain legacy PO values in the database for historical recovery, not in the UI or exports.
    with op.batch_alter_table("assigned_sites") as batch:
        batch.drop_column("employee_id")
    with op.batch_alter_table("invoice") as batch:
        batch.add_column(sa.Column("po_number", sa.String(length=120), nullable=False, server_default=""))
        batch.add_column(sa.Column("po_date", sa.Date()))


def downgrade():
    raise RuntimeError(
        "Shared sites cannot be downgraded without losing multiple engineer assignments. "
        "Restore the pre-upgrade database backup to roll back."
    )
