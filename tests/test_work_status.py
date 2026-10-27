import io
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flask_migrate import downgrade, upgrade
from openpyxl import load_workbook
from sqlalchemy import inspect

from geosite import create_app
from geosite.attendance import daily_cutoff
from geosite.extensions import db
from geosite.models import Attendance, AttendancePolicy, AssignedSite, AuditLog, DailyWorkStatus, LeaveRequest, SiteAssignment, User
from geosite.timeutils import INDIA_TIME
from geosite.work_status import attendance_summary, month_bounds


class WorkStatusTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "work-status-tests",
            "SQLALCHEMY_DATABASE_URI": "sqlite://", "UPLOAD_FOLDER": self.uploads.name,
            "WTF_CSRF_ENABLED": False,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.employee = self.user("Engineer")
        self.other = self.user("Other")
        self.admin = self.user("Admin", "admin")
        self.site = AssignedSite(site_name="Site", customer_name="Customer", address="Address",
                                 latitude=19, longitude=72, allowed_radius=100)
        db.session.add_all([self.site, AttendancePolicy(id=1, effective_from=date(2026, 10, 26))])
        db.session.flush()
        db.session.add(SiteAssignment(employee=self.employee, site=self.site))
        db.session.commit()
        self.client = self.app.test_client()
        self.now = datetime(2026, 10, 27, 10, tzinfo=timezone.utc)
        self.patches = [
            patch("geosite.routes.utcnow", side_effect=lambda: self.now),
            patch("geosite.work_status.utcnow", side_effect=lambda: self.now),
            patch("geosite.attendance.utcnow", side_effect=lambda: self.now),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.uploads.cleanup()

    def user(self, name, role="employee"):
        user = User(name=name, email=f"{name.lower()}@example.com", mobile=name, role=role,
                    created_date=datetime(2026, 10, 1, tzinfo=timezone.utc))
        user.set_password("work-status-password")
        db.session.add(user)
        db.session.flush()
        return user

    def login(self, user):
        self.client.post("/logout")
        self.client.post("/login", data={"email": user.email, "password": "work-status-password"})

    def submit(self, **values):
        return self.client.post("/employee/work-status", data={
            "work_type": "wfh", "reason": "Preparing documentation", **values,
        }, follow_redirects=True)

    def visit(self, when=None, **values):
        visit = Attendance(
            employee_id=self.employee.id, site_id=self.site.id,
            checkin_time=when or self.now, checkin_lat=19, checkin_lon=72,
            checkin_selfie="test.png", status="onsite",
            site_latitude=19, site_longitude=72, site_radius_m=100, **values,
        )
        db.session.add(visit)
        db.session.commit()
        return visit

    def leave(self, start, end=None, status="approved", period="full", employee=None):
        leave = LeaveRequest(employee_id=(employee or self.employee).id, start_date=start,
                             end_date=end or start, status=status, period=period, reason="Leave reason")
        db.session.add(leave)
        db.session.commit()
        return leave

    def summary(self):
        return attendance_summary(self.employee, *month_bounds(date(2026, 10, 27)), now=self.now)

    def test_submit_today_pending_and_admin_approval_removes_automatic_leave(self):
        self.login(self.employee)
        response = self.submit(employee_id=self.other.id, work_date="2026-01-01")
        self.assertIn(b"submitted for administrator approval", response.data)
        item = DailyWorkStatus.query.one()
        self.assertEqual(item.employee_id, self.employee.id)
        self.assertEqual(item.work_date, date(2026, 10, 27))
        self.assertEqual(item.status, "pending")
        self.assertIn(b"WFH", response.data)
        self.assertIsNotNone(AuditLog.query.filter_by(action="work_status_submitted").first())
        self.now = daily_cutoff(self.now)
        self.assertEqual(self.summary()["totals"]["automatic"], 2)
        self.login(self.admin)
        page = self.client.get("/admin/work-status?status=pending")
        self.assertIn(b"Preparing documentation", page.data)
        response = self.client.post(f"/admin/work-status/{item.id}/review",
                                    data={"decision": "approved"}, follow_redirects=True)
        self.assertIn(b"Attendance totals now reflect", response.data)
        db.session.refresh(item)
        self.assertEqual(item.status, "approved")
        self.assertEqual(item.reviewed_by_id, self.admin.id)
        self.assertEqual(self.summary()["totals"]["automatic"], 1)
        self.client.post(f"/admin/work-status/{item.id}/review", data={"decision": "rejected"})
        db.session.refresh(item)
        self.assertEqual(item.status, "approved")
        self.login(self.employee)
        page = self.client.get("/employee")
        self.assertIn(b'id="monthly-leave-automatic">1', page.data)

    def test_types_reasons_duplicates_and_access_controls(self):
        self.assertEqual(self.submit().status_code, 200)  # Redirected to login.
        self.assertEqual(DailyWorkStatus.query.count(), 0)
        self.login(self.employee)
        for values in ({"work_type": "invalid"}, {"reason": ""}, {"reason": " "}, {"reason": "x" * 2001}):
            self.assertIn(b"Select WFH", self.submit(**values).data)
        self.assertEqual(DailyWorkStatus.query.count(), 0)
        self.submit(work_type="travelling")
        self.assertEqual(DailyWorkStatus.query.one().type_label, "Travelling")
        self.assertIn(b"already been submitted", self.submit(work_type="training").data)
        self.assertEqual(DailyWorkStatus.query.count(), 1)
        self.assertEqual(self.client.get("/admin/work-status").status_code, 403)
        item = DailyWorkStatus.query.one()
        self.assertEqual(self.client.post(f"/admin/work-status/{item.id}/review",
                                         data={"decision": "approved"}).status_code, 403)
        self.login(self.other)
        self.assertNotIn(b"Preparing documentation", self.client.get("/employee").data)
        self.login(self.admin)
        self.assertEqual(self.client.post("/employee/work-status").status_code, 403)
        self.assertEqual(self.client.get("/admin/work-status?status=bad").status_code, 400)
        self.assertEqual(self.client.post(f"/admin/work-status/{item.id}/review",
                                         data={"decision": "bad"}).status_code, 400)
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.client.post(f"/admin/work-status/{item.id}/review",
                                         data={"decision": "approved"}).status_code, 400)

    def test_ist_cutoff_monday_to_saturday_policy_and_no_historical_backfill(self):
        # Monday is the policy start; Sunday and earlier dates must never accrue.
        self.now = datetime(2026, 10, 26, 18, 28, 59, tzinfo=timezone.utc)
        self.assertEqual(self.summary()["totals"]["automatic"], 0)
        self.now += timedelta(seconds=1)
        self.assertEqual(self.summary()["totals"]["automatic"], 1)
        self.now = datetime(2026, 11, 1, 18, 29, tzinfo=timezone.utc)  # Sunday.
        self.assertEqual(self.summary()["totals"]["automatic"], 6)
        db.session.get(AttendancePolicy, 1).effective_from = date(2026, 10, 30)
        db.session.commit()
        self.assertEqual(self.summary()["totals"]["automatic"], 2)
        self.employee.created_date = datetime(2026, 10, 31, 0, tzinfo=timezone.utc)
        db.session.commit()
        self.assertEqual(self.summary()["totals"]["automatic"], 1)

    def test_site_visit_even_stopped_without_report_prevents_automatic_leave(self):
        self.visit(when=datetime(2026, 10, 26, 20, tzinfo=timezone.utc))
        self.now = daily_cutoff(self.now)
        # 26 Oct 20:00 UTC is 27 Oct in India.
        self.assertEqual(self.summary()["totals"]["automatic"], 1)
        self.now -= timedelta(seconds=1)
        self.login(self.employee)
        self.assertIn(b"already recorded today", self.submit().data)

    def test_approved_half_leave_is_not_double_counted_and_rejected_work_is_leave(self):
        self.leave(date(2026, 10, 26), period="morning")
        self.now = daily_cutoff(self.now)
        summary = self.summary()
        self.assertEqual(summary["totals"]["approved"], 0.5)
        self.assertEqual(summary["totals"]["automatic"], 1.5)
        self.assertEqual(summary["total_leave"], 2)
        self.leave(date(2026, 10, 26), period="afternoon")
        self.assertEqual(self.summary()["totals"]["automatic"], 1)
        db.session.add(DailyWorkStatus(employee_id=self.employee.id, work_date=date(2026, 10, 27),
                                       work_type="training", reason="Course", status="rejected"))
        db.session.commit()
        self.assertEqual(self.summary()["totals"]["automatic"], 1)

    def test_monthly_totals_clip_cross_month_requests_and_list_all_statuses(self):
        self.leave(date(2026, 9, 29), date(2026, 10, 2))
        self.leave(date(2026, 10, 30), date(2026, 11, 3), status="pending")
        self.leave(date(2026, 10, 3), status="rejected", period="morning")
        self.leave(date(2026, 10, 4), employee=self.other)
        summary = self.summary()
        self.assertEqual(summary["totals"]["approved"], 2)
        self.assertEqual(summary["totals"]["pending"], 2)
        self.assertEqual(summary["totals"]["rejected"], 0.5)
        self.login(self.employee)
        page = self.client.get("/employee")
        self.assertIn(b"My leave - October 2026", page.data)
        for status in ("approved", "pending", "rejected", "automatic"):
            self.assertIn(f'id="monthly-leave-{status}"'.encode(), page.data)
        self.assertIn(b"Start Site", page.data)
        self.assertNotIn(b"Start site visit", page.data)
        self.visit()
        page = self.client.get("/employee")
        self.assertIn(b"Stop Site", page.data)
        self.assertNotIn(b"End site visit", page.data)

    def test_deadline_rejects_submission_at_2359_and_training_is_valid_before(self):
        self.login(self.employee)
        self.now = daily_cutoff(self.now)
        self.assertIn(b"deadline (23:59 IST) has passed", self.submit().data)
        self.assertEqual(DailyWorkStatus.query.count(), 0)
        self.now -= timedelta(seconds=1)
        self.submit(work_type="training")
        self.assertEqual(DailyWorkStatus.query.one().type_label, "Training")

    def test_monitor_token_scoped_survives_logout_and_detects_stop(self):
        visit = self.visit()
        self.login(self.employee)
        response = self.client.get("/api/employee/active-visit")
        data = response.json
        self.assertEqual(data["attendance_id"], visit.id)
        self.assertEqual(data["radius_m"], 100)
        self.assertEqual(data["latitude"], 19)
        self.assertEqual(data["cutoff_time"], daily_cutoff(self.now).isoformat())
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.site.allowed_radius = 999
        db.session.commit()
        self.assertEqual(self.client.get("/api/employee/active-visit").json["radius_m"], 100)
        token = data["monitoring_token"]
        self.client.post("/logout")
        headers = {"Authorization": f"Bearer {token}"}
        response = self.client.get("/api/visit-monitor", headers=headers)
        self.assertTrue(response.json["active"])
        self.assertNotIn("monitoring_token", response.json)
        visit.status = "report_pending"
        db.session.commit()
        self.assertEqual(self.client.get("/api/visit-monitor", headers=headers).json, {"active": False})

    def test_automatic_leave_flows_to_leave_totals_and_excel_without_double_counting(self):
        self.now = daily_cutoff(self.now)
        self.leave(date(2026, 10, 26), period="morning")
        self.login(self.employee)
        page = self.client.get("/employee/leaves?from_date=2026-10-26&to_date=2026-10-27")
        self.assertIn(b'id="leave-total">2', page.data)
        self.assertIn(b"Automatic leave", page.data)
        params = {"site_id": self.site.id, "start_date": "2026-10-26", "end_date": "2026-10-27"}
        response = self.client.get("/reports/activity.xlsx", query_string=params)
        sheet = load_workbook(io.BytesIO(response.data)).active
        details = [row[4].value for row in sheet.iter_rows(min_row=9)]
        self.assertIn("Automatic leave (0.5 day): no site visit or approved work confirmation.", details)
        self.assertIn("Automatic leave (1 day): no site visit or approved work confirmation.", details)
        self.login(self.admin)
        page = self.client.get(f"/admin/leaves?employee_id={self.employee.id}&from_date=2026-10-26&to_date=2026-10-27")
        self.assertIn(b'id="leave-total">2', page.data)
        params["employee_id"] = self.employee.id
        response = self.client.get("/reports/activity.xlsx", query_string=params)
        self.assertEqual(list(load_workbook(io.BytesIO(response.data)).active.values), list(sheet.values))

    def test_monitor_auth_expiry_tampering_ownership_and_disabled_employee(self):
        self.visit()
        self.login(self.other)
        self.assertEqual(self.client.get("/api/employee/active-visit").json, {"active": False})
        self.login(self.admin)
        self.assertEqual(self.client.get("/api/employee/active-visit").status_code, 403)
        self.login(self.employee)
        token = self.client.get("/api/employee/active-visit").json["monitoring_token"]
        self.client.post("/logout")
        for headers in ({}, {"Authorization": "Bearer bad"}, {"Authorization": f"Bearer {token}x"}):
            self.assertEqual(self.client.get("/api/visit-monitor", headers=headers).status_code, 401)
        self.assertEqual(self.client.get(f"/api/visit-monitor?token={token}").status_code, 401)
        self.employee.is_active = False
        db.session.commit()
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/api/visit-monitor", headers=headers).status_code, 401)
        self.employee.is_active = True
        db.session.commit()
        self.now = daily_cutoff(self.now)
        self.assertEqual(self.client.get("/api/visit-monitor", headers=headers).status_code, 401)


class WorkStatusMigrationTests(unittest.TestCase):
    def test_upgrade_policy_activation_and_reversible_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "SECRET_KEY": "work-status-migration",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(directory) / "work.db"),
                "UPLOAD_FOLDER": str(Path(directory) / "uploads"),
            })
            migrations = str(Path(__file__).resolve().parents[1] / "migrations")
            with app.app_context():
                try:
                    upgrade(directory=migrations, revision="e48f9a0b1c27")
                    user = User(name="Old", email="old@example.com", mobile="1", password_hash="test")
                    db.session.add(user)
                    db.session.commit()
                    upgrade(directory=migrations)
                    self.assertEqual(db.session.get(AttendancePolicy, 1).effective_from,
                                     datetime.now(INDIA_TIME).date())
                    self.assertEqual(DailyWorkStatus.query.count(), 0)
                    self.assertIn("site_radius_m", {
                        column["name"] for column in inspect(db.engine).get_columns("attendance")
                    })
                    db.session.remove()
                    downgrade(directory=migrations, revision="e48f9a0b1c27")
                    self.assertNotIn("daily_work_status", inspect(db.engine).get_table_names())
                    self.assertEqual(User.query.one().name, "Old")
                    upgrade(directory=migrations)
                    self.assertEqual(DailyWorkStatus.query.count(), 0)
                finally:
                    db.session.remove()
                    db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
