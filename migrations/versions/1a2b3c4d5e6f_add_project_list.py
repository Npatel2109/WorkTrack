"""Add a reusable project list and link projects to sites.

Revision ID: 1a2b3c4d5e6f
Revises: f60a0b1c2d39
"""
from alembic import op
import sqlalchemy as sa


revision = "1a2b3c4d5e6f"
down_revision = "f60a0b1c2d39"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=250), nullable=False, unique=True),
    )
    with op.batch_alter_table("assigned_sites") as batch:
        batch.add_column(
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True)
        )
    op.create_index("ix_assigned_sites_project_id", "assigned_sites", ["project_id"])

    bind = op.get_bind()
    sites = sa.table(
        "assigned_sites",
        sa.column("id", sa.Integer()),
        sa.column("project_name", sa.String(length=250)),
        sa.column("project_id", sa.Integer()),
        sa.column("report_company", sa.String(length=160)),
    )
    project_table = sa.table(
        "project",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String(length=250)),
    )
    rows = bind.execute(
        sa.select(sites.c.id, sites.c.project_name)
    ).all()
    project_ids = {}
    for site_id, raw_name in rows:
        name = (raw_name or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key not in project_ids:
            result = bind.execute(
                project_table.insert().values(name=name)
            )
            project_ids[key] = result.inserted_primary_key[0]
        bind.execute(
            sites.update()
            .where(sites.c.id == site_id)
            .values(project_id=project_ids[key], project_name=name)
        )
    bind.execute(
        sites.update().values(report_company="Blackpurple automation")
    )


def downgrade():
    op.drop_index("ix_assigned_sites_project_id", table_name="assigned_sites")
    with op.batch_alter_table("assigned_sites") as batch:
        batch.drop_column("project_id")
    op.drop_table("project")
