"""Add referral resumes and remove position.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""
from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("candidate_referral") as batch_op:
        batch_op.add_column(sa.Column("resume_filename", sa.String(length=40), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("resume_original_filename", sa.String(length=255), nullable=False, server_default=""))
        batch_op.drop_column("position")


def downgrade():
    with op.batch_alter_table("candidate_referral") as batch_op:
        batch_op.drop_column("resume_original_filename")
        batch_op.drop_column("resume_filename")
        batch_op.add_column(sa.Column("position", sa.String(length=120), nullable=False, server_default=""))
