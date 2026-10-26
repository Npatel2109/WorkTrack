"""Add attendance location provenance, distance snapshots and daily closure.

Revision ID: c26d7e8f9a05
Revises: b15c6d7e8f94
"""
from alembic import op
import sqlalchemy as sa


revision = "c26d7e8f9a05"
down_revision = "b15c6d7e8f94"
branch_labels = None
depends_on = None

FLOAT_COLUMNS = (
    "site_latitude", "site_longitude", "checkin_distance_m", "checkout_distance_m",
    "last_location_lat", "last_location_lon",
)
TIME_COLUMNS = ("checkin_location_time", "last_location_time", "checkout_location_time")


def upgrade():
    with op.batch_alter_table("attendance") as batch:
        for name in FLOAT_COLUMNS:
            batch.add_column(sa.Column(name, sa.Float()))
        for name in TIME_COLUMNS:
            batch.add_column(sa.Column(name, sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("checkout_location_source", sa.String(length=20)))
        batch.add_column(sa.Column("checkout_location_reason", sa.String(length=40)))
        batch.add_column(sa.Column("auto_closed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("""
        UPDATE attendance SET
            site_latitude = (SELECT latitude FROM assigned_sites WHERE assigned_sites.id = attendance.site_id),
            site_longitude = (SELECT longitude FROM assigned_sites WHERE assigned_sites.id = attendance.site_id),
            last_location_lat = checkin_lat,
            last_location_lon = checkin_lon,
            last_location_time = checkin_time,
            checkout_location_source = CASE WHEN checkout_time IS NOT NULL THEN 'legacy' ELSE NULL END
    """)


def downgrade():
    # The previous application does not understand stopped visits without reports.
    op.execute("UPDATE attendance SET status = 'completed' WHERE status = 'report_pending'")
    with op.batch_alter_table("attendance") as batch:
        for name in ("auto_closed", "checkout_location_reason", "checkout_location_source"):
            batch.drop_column(name)
        for name in reversed(TIME_COLUMNS + FLOAT_COLUMNS):
            batch.drop_column(name)
