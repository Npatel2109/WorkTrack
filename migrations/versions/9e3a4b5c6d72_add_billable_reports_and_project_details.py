"""Add billable reports and admin project details."""
from alembic import op
import sqlalchemy as sa

revision = "9e3a4b5c6d72"
down_revision = "8d2f3a4b5c61"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("site_report") as batch:
        batch.add_column(sa.Column("is_billable", sa.Boolean(), nullable=True))
    with op.batch_alter_table("assigned_sites") as batch:
        batch.add_column(sa.Column("project_name", sa.String(250), nullable=False, server_default=""))
        batch.add_column(sa.Column("po_number", sa.String(120), nullable=False, server_default=""))
        batch.add_column(sa.Column("po_date", sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table("assigned_sites") as batch:
        batch.drop_column("po_date")
        batch.drop_column("po_number")
        batch.drop_column("project_name")
    with op.batch_alter_table("site_report") as batch:
        batch.drop_column("is_billable")
