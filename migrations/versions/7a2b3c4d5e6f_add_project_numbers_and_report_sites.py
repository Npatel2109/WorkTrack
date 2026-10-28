"""Add project numbers and site references to submitted reports.

Revision ID: 7a2b3c4d5e6f
Revises: 06ab1c2d3e49, 1a2b3c4d5e6f
"""
from alembic import op
import sqlalchemy as sa


revision = "7a2b3c4d5e6f"
down_revision = ("06ab1c2d3e49", "1a2b3c4d5e6f")
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("project", sa.Column("project_number", sa.String(length=80), nullable=True))
    bind = op.get_bind()
    project = sa.table(
        "project",
        sa.column("id", sa.Integer()),
        sa.column("project_number", sa.String(length=80)),
    )
    for (project_id,) in bind.execute(sa.select(project.c.id)):
        bind.execute(
            project.update()
            .where(project.c.id == project_id)
            .values(project_number=f"LEGACY-{project_id}")
        )
    with op.batch_alter_table("project", recreate="always") as batch:
        batch.alter_column(
            "project_number",
            existing_type=sa.String(length=80),
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_project_project_number", ["project_number"]
        )

    with op.batch_alter_table("submitted_report", recreate="always") as batch:
        batch.add_column(
            sa.Column("site_id", sa.Integer(), sa.ForeignKey("assigned_sites.id"), nullable=True)
        )
    op.create_index("ix_submitted_report_site_id", "submitted_report", ["site_id"])


def downgrade():
    op.drop_index("ix_submitted_report_site_id", table_name="submitted_report")
    with op.batch_alter_table("submitted_report") as batch:
        batch.drop_column("site_id")
    with op.batch_alter_table("project", recreate="always") as batch:
        batch.drop_constraint("uq_project_project_number", type_="unique")
        batch.drop_column("project_number")
