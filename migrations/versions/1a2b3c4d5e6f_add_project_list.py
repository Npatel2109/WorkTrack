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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_sites = inspector.has_table("assigned_sites")
    has_batch_table = inspector.has_table("_alembic_tmp_assigned_sites")
    if has_batch_table:
        if has_sites:
            op.drop_table("_alembic_tmp_assigned_sites")
        else:
            op.rename_table("_alembic_tmp_assigned_sites", "assigned_sites")

    inspector = sa.inspect(bind)
    if not inspector.has_table("project"):
        op.create_table(
            "project",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=250), nullable=False, unique=True),
        )

    inspector = sa.inspect(bind)
    site_columns = {column["name"] for column in inspector.get_columns("assigned_sites")}
    if "project_id" not in site_columns:
        with op.batch_alter_table("assigned_sites") as batch:
            batch.add_column(
                sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True)
            )

    inspector = sa.inspect(bind)
    site_indexes = {index["name"] for index in inspector.get_indexes("assigned_sites")}
    if "ix_assigned_sites_project_id" not in site_indexes:
        op.create_index("ix_assigned_sites_project_id", "assigned_sites", ["project_id"])

    sites = sa.table(
        "assigned_sites",
        sa.column("id", sa.Integer()),
        sa.column("project_name", sa.String(length=250)),
        sa.column("project_id", sa.Integer()),
        sa.column("report_company", sa.String(length=160)),
    )
    project_table = sa.table(
        "project",
        sa.column("id", sa.Integer(), primary_key=True),
        sa.column("name", sa.String(length=250)),
    )
    rows = bind.execute(
        sa.select(sites.c.id, sites.c.project_name)
    ).all()
    project_ids = {}
    existing_projects = bind.execute(
        sa.select(project_table.c.id, project_table.c.name)
    )
    for project_id, raw_name in existing_projects:
        name = (raw_name or "").strip()
        if name:
            project_ids.setdefault(name.casefold(), project_id)
    for site_id, raw_name in rows:
        name = (raw_name or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key not in project_ids:
            bind.execute(project_table.insert().values(name=name))
            project_ids[key] = bind.execute(
                sa.select(project_table.c.id).where(project_table.c.name == name)
            ).scalar_one()
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
