"""Add admin report sign-off details."""
from alembic import op
import sqlalchemy as sa

revision = "a04b5c6d7e83"
down_revision = "9e3a4b5c6d72"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("assigned_sites") as batch:
        for name, length in (
            ("report_company", 160), ("commissioning_manager", 120), ("project_manager", 120),
        ):
            batch.add_column(sa.Column(name, sa.String(length), nullable=False, server_default=""))
        batch.add_column(sa.Column("report_remarks", sa.Text(), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("assigned_sites") as batch:
        for name in ("report_remarks", "project_manager", "commissioning_manager", "report_company"):
            batch.drop_column(name)
