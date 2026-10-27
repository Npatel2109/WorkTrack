import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.orm import defer

from geosite import create_app
from geosite.extensions import db
from geosite.models import AssignedSite, Attendance, Invoice, SiteAssignment, User


class AttendanceMigrationTests(unittest.TestCase):
    def test_shared_site_upgrade_preserves_distinct_sites_assignments_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "SECRET_KEY": "migration-test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(directory) / "shared.db"),
                "UPLOAD_FOLDER": str(Path(directory) / "uploads"),
            })
            migrations = str(Path(__file__).resolve().parents[1] / "migrations")
            with app.app_context():
                try:
                    upgrade(directory=migrations, revision="c26d7e8f9a05")
                    user = User(name="Engineer", email="migration@example.com", mobile="1", password_hash="test")
                    db.session.add(user)
                    db.session.flush()
                    for site_id in (1, 2, 3):
                        db.session.execute(text("""
                            INSERT INTO assigned_sites
                            (id, employee_id, site_name, customer_name, address, latitude, longitude,
                             allowed_radius, assigned_date, is_active, po_number, po_date)
                            VALUES (:id, :user, 'Same site', 'Customer', 'Address', 19, 72,
                                    100, :assigned, :active, 'LEGACY-PO', '2026-10-01')
                        """), {"id": site_id, "user": user.id, "assigned": "2026-10-20 05:00:00",
                               "active": 0 if site_id == 3 else 1})
                    db.session.execute(text("""
                        INSERT INTO attendance
                        (id, employee_id, site_id, checkin_time, checkin_lat, checkin_lon,
                         checkin_selfie, status, checkin_distance_m, site_latitude, site_longitude)
                        VALUES (1, :user, 1, '2026-10-20 05:00:00', 19.01, 72.01,
                                'selfie.png', 'onsite', 1234.5, 19, 72)
                    """), {"user": user.id})
                    db.session.execute(text("""
                        INSERT INTO invoice
                        (id, invoice_number, employee_id, site_id, customer_name, description,
                         start_date, end_date, billable_days, daily_rate, total_amount, currency,
                         created_at, created_by_id)
                        VALUES (1, 'OLD-INVOICE', :user, 1, 'Customer', 'Historical service',
                                '2026-10-01', '2026-10-01', 1, 100, 100, 'INR',
                                '2026-10-20 05:00:00', :user)
                    """), {"user": user.id})
                    db.session.commit()
                    upgrade(directory=migrations)
                    self.assertEqual(AssignedSite.query.count(), 3)
                    self.assertEqual(SiteAssignment.query.count(), 3)
                    self.assertEqual(SiteAssignment.query.filter_by(is_active=True).count(), 2)
                    self.assertEqual(SiteAssignment.query.filter_by(site_id=3).one().employee_id, user.id)
                    self.assertFalse(SiteAssignment.query.filter_by(site_id=3).one().is_active)
                    visit = db.session.get(Attendance, 1)
                    self.assertEqual((visit.site_id, visit.employee_id, visit.checkin_distance_m),
                                     (1, user.id, 1234.5))
                    invoice = db.session.get(Invoice, 1)
                    self.assertEqual(invoice.invoice_number, "OLD-INVOICE")
                    self.assertEqual(invoice.employee_id, user.id)
                    self.assertEqual(invoice.po_number, "")
                    self.assertIsNone(invoice.po_date)
                    self.assertEqual(db.session.execute(text(
                        "SELECT po_number FROM assigned_sites WHERE id = 1"
                    )).scalar_one(), "LEGACY-PO")
                    self.assertNotIn("employee_id", {
                        column["name"] for column in inspect(db.engine).get_columns("assigned_sites")
                    })
                    other = User(name="Other", email="other@example.com", mobile="2", password_hash="test")
                    db.session.add(other)
                    db.session.flush()
                    db.session.add(SiteAssignment(employee=other, site=db.session.get(AssignedSite, 1)))
                    db.session.commit()
                    self.assertEqual(AssignedSite.query.count(), 3)
                    self.assertEqual(SiteAssignment.query.filter_by(site_id=1).count(), 2)
                finally:
                    db.session.remove()
                    db.engine.dispose()

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
                    db.session.execute(text("""
                        INSERT INTO assigned_sites
                        (id, employee_id, site_name, customer_name, address, latitude, longitude,
                         allowed_radius, assigned_date, is_active)
                        VALUES (1, :user, 'Site', 'Customer', 'Address', 19, 72, 100, :assigned, 1)
                    """), {"user": user.id, "assigned": "2026-10-20 05:00:00"})
                    db.session.execute(text("""
                        INSERT INTO attendance
                        (id, employee_id, site_id, checkin_time, checkout_time,
                         checkin_lat, checkin_lon, checkout_lat, checkout_lon, checkin_selfie, status)
                        VALUES (1, :user, :site, :started, :stopped, 19.01, 72.01, 19.02, 72.02, 'selfie.png', 'completed'),
                               (2, :user, :site, :started, NULL, 19.01, 72.01, NULL, NULL, 'selfie.png', 'onsite')
                    """), {
                        "user": user.id, "site": 1,
                        "started": datetime(2026, 10, 20, 5, tzinfo=timezone.utc).isoformat(),
                        "stopped": datetime(2026, 10, 20, 6, tzinfo=timezone.utc).isoformat(),
                    })
                    db.session.commit()
                    upgrade(directory=migrations, revision="c26d7e8f9a05")
                    completed = db.session.get(Attendance, 1, options=[defer(Attendance.site_radius_m)])
                    self.assertEqual(completed.checkout_lon, 72.02)
                    self.assertEqual(completed.checkout_location_source, "legacy")
                    self.assertEqual(completed.site_latitude, 19)
                    self.assertIsNone(completed.checkin_distance_m)
                    active = db.session.get(Attendance, 2, options=[defer(Attendance.site_radius_m)])
                    self.assertEqual(active.last_location_lon, 72.01)
                    self.assertEqual(active.last_location_time, active.checkin_time)
                    self.assertFalse(active.auto_closed)
                    db.session.remove()
                    downgrade(directory=migrations, revision="b15c6d7e8f94")
                    columns = {column["name"] for column in inspect(db.engine).get_columns("attendance")}
                    self.assertNotIn("last_location_time", columns)
                    upgrade(directory=migrations, revision="c26d7e8f9a05")
                    self.assertEqual(db.session.get(
                        Attendance, 1, options=[defer(Attendance.site_radius_m)]
                    ).checkout_lon, 72.02)
                finally:
                    db.session.remove()
                    db.engine.dispose()
