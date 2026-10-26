import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from geosite import create_app
from geosite.extensions import db
from geosite.models import AssignedSite, Attendance, User


class AttendanceMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_history_and_downgrade_can_be_reapplied(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "SECRET_KEY": "migration-test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(directory) / "migration.db"),
                "UPLOAD_FOLDER": str(Path(directory) / "uploads"),
            })
            migrations = str(Path(__file__).resolve().parents[1] / "migrations")
            with app.app_context():
                try:
                    upgrade(directory=migrations, revision="b15c6d7e8f94")
                    user = User(name="Engineer", email="migration@example.com", mobile="1", password_hash="test")
                    db.session.add(user)
                    db.session.flush()
                    site = AssignedSite(
                        employee_id=user.id, site_name="Site", customer_name="Customer",
                        address="Address", latitude=19, longitude=72,
                    )
                    db.session.add(site)
                    db.session.flush()
                    db.session.execute(text("""
                        INSERT INTO attendance
                        (id, employee_id, site_id, checkin_time, checkout_time,
                         checkin_lat, checkin_lon, checkout_lat, checkout_lon, checkin_selfie, status)
                        VALUES (1, :user, :site, :started, :stopped, 19.01, 72.01, 19.02, 72.02, 'selfie.png', 'completed'),
                               (2, :user, :site, :started, NULL, 19.01, 72.01, NULL, NULL, 'selfie.png', 'onsite')
                    """), {
                        "user": user.id, "site": site.id,
                        "started": datetime(2026, 10, 20, 5, tzinfo=timezone.utc).isoformat(),
                        "stopped": datetime(2026, 10, 20, 6, tzinfo=timezone.utc).isoformat(),
                    })
                    db.session.commit()
                    upgrade(directory=migrations)
                    completed = db.session.get(Attendance, 1)
                    self.assertEqual(completed.checkout_lon, 72.02)
                    self.assertEqual(completed.checkout_location_source, "legacy")
                    self.assertEqual(completed.site_latitude, 19)
                    self.assertIsNone(completed.checkin_distance_m)
                    active = db.session.get(Attendance, 2)
                    self.assertEqual(active.last_location_lon, 72.01)
                    self.assertEqual(active.last_location_time, active.checkin_time)
                    self.assertFalse(active.auto_closed)
                    db.session.remove()
                    downgrade(directory=migrations, revision="b15c6d7e8f94")
                    columns = {column["name"] for column in inspect(db.engine).get_columns("attendance")}
                    self.assertNotIn("last_location_time", columns)
                    upgrade(directory=migrations)
                    self.assertEqual(db.session.get(Attendance, 1).checkout_lon, 72.02)
                finally:
                    db.session.remove()
                    db.engine.dispose()
