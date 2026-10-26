import tempfile
import unittest
from datetime import timedelta

from geosite import create_app
from geosite.extensions import db
from geosite.models import Attendance, AssignedSite, AuditLog, LeaveRequest, User, utcnow


class LeaveTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "leave-test-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "UPLOAD_FOLDER": self.uploads.name, "WTF_CSRF_ENABLED": False,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.admin = self.make_user("Admin", "admin")
        self.employee = self.make_user("Engineer")
        self.other = self.make_user("Other")
        db.session.commit()
        self.client = self.app.test_client()
        self.today = utcnow().date()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.uploads.cleanup()

    def make_user(self, name, role="employee"):
        user = User(
            name=name, email=f"{name.lower()}@example.com", mobile="100",
            designation="Engineer", role=role,
        )
        user.set_password("test-password-123")
        db.session.add(user)
        db.session.flush()
        return user

    def login(self, user):
        self.client.post("/logout")
        return self.client.post("/login", data={
            "email": user.email, "password": "test-password-123",
        })

    def apply(self, **values):
        data = {
            "start_date": self.today.isoformat(), "end_date": self.today.isoformat(),
            "period": "full", "reason": "Personal appointment",
        }
        data.update(values)
        return self.client.post("/employee/leaves", data=data, follow_redirects=True)

    def add_leave(self, employee=None, **values):
        data = {
            "employee_id": (employee or self.employee).id,
            "start_date": self.today, "end_date": self.today,
            "period": "full", "reason": "Personal appointment", "status": "pending",
        }
        data.update(values)
        leave = LeaveRequest(**data)
        db.session.add(leave)
        db.session.commit()
        return leave

    def test_apply_approve_and_dashboard_lists(self):
        self.login(self.employee)
        response = self.apply(end_date=(self.today + timedelta(days=2)).isoformat())
        self.assertIn(b"submitted for administrator approval", response.data)
        leave = LeaveRequest.query.one()
        self.assertEqual(leave.days, 3)
        self.assertEqual(leave.status, "pending")
        self.assertIsNotNone(AuditLog.query.filter_by(action="leave_requested").first())
        self.assertIn(b"Pending", self.client.get("/employee").data)
        self.login(self.admin)
        dashboard = self.client.get("/admin")
        self.assertIn(b"/admin/leaves?status=pending", dashboard.data)
        self.assertIn(b"/admin/leaves?status=today", dashboard.data)
        self.assertEqual(self.client.get("/api/admin/live-status").json["pending_leave_count"], 1)
        self.assertIn(b"Personal appointment", self.client.get("/admin/leaves?status=pending").data)
        response = self.client.post(
            f"/admin/leaves/{leave.id}/review", data={"decision": "approved"},
            follow_redirects=True,
        )
        self.assertIn(b"Leave request approved.", response.data)
        db.session.refresh(leave)
        self.assertEqual(leave.reviewed_by_id, self.admin.id)
        self.assertIsNotNone(leave.reviewed_at)
        data = self.client.get("/api/admin/live-status").json
        self.assertEqual(data["pending_leave_count"], 0)
        self.assertEqual(data["on_leave_count"], 1)
        self.assertEqual(next(row for row in data["employees"] if row["name"] == "Engineer")["status"], "on-leave")
        self.assertIn(b"Personal appointment", self.client.get("/admin/leaves?status=today").data)
        self.login(self.employee)
        response = self.client.get("/employee/leaves")
        self.assertIn(b"Approved", response.data)
        self.assertIn(b"Admin", response.data)

    def test_half_days_and_overlap_validation(self):
        self.login(self.employee)
        self.apply(period="morning")
        self.assertEqual(LeaveRequest.query.one().days, 0.5)
        self.assertIn(b"overlap", self.apply(period="morning").data)
        self.assertIn(b"overlap", self.apply(period="full").data)
        self.apply(period="afternoon")
        self.assertEqual(LeaveRequest.query.count(), 2)
        for leave in LeaveRequest.query:
            leave.status = "approved"
        db.session.commit()
        self.login(self.admin)
        self.assertEqual(self.client.get("/api/admin/live-status").json["on_leave_count"], 1)
        self.assertEqual(self.client.get("/admin/leaves?status=today").status_code, 200)

    def test_invalid_requests_preserve_input_and_do_not_create_records(self):
        self.login(self.employee)
        for values in (
            {"start_date": "bad"}, {"end_date": ""},
            {"start_date": (self.today - timedelta(days=1)).isoformat()},
            {"start_date": (self.today + timedelta(days=2)).isoformat()},
            {"period": "morning", "end_date": (self.today + timedelta(days=1)).isoformat()},
            {"period": "invalid"}, {"reason": "   "}, {"reason": "x" * 2001},
        ):
            with self.subTest(values=values):
                response = self.apply(**values)
                self.assertIn(b"Invalid leave request", response.data)
                self.assertEqual(LeaveRequest.query.count(), 0)

    def test_rejection_and_decisions_are_final(self):
        leave = self.add_leave()
        self.login(self.admin)
        url = f"/admin/leaves/{leave.id}/review"
        self.assertEqual(self.client.post(url, data={"decision": "invalid"}).status_code, 400)
        self.client.post(url, data={"decision": "rejected"})
        response = self.client.post(url, data={"decision": "approved"}, follow_redirects=True)
        self.assertIn(b"already been reviewed", response.data)
        db.session.refresh(leave)
        self.assertEqual(leave.status, "rejected")
        self.assertEqual(AuditLog.query.filter(AuditLog.action.in_(("leave_approved", "leave_rejected"))).count(), 1)
        self.assertIn(b"Personal appointment", self.client.get("/admin/leaves?status=rejected").data)
        self.login(self.employee)
        self.apply()
        self.assertEqual(LeaveRequest.query.count(), 2)

    def test_role_access_ownership_and_csrf(self):
        leave = self.add_leave(reason="Private reason")
        self.assertEqual(self.client.get("/employee/leaves").status_code, 302)
        self.login(self.other)
        self.assertNotIn(b"Private reason", self.client.get("/employee/leaves").data)
        self.assertNotIn(b"Private reason", self.client.get("/employee").data)
        self.assertEqual(self.client.get("/admin/leaves").status_code, 403)
        self.assertEqual(self.client.post(
            f"/admin/leaves/{leave.id}/review", data={"decision": "approved"},
        ).status_code, 403)
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.client.post("/employee/leaves", data={}).status_code, 400)
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.login(self.admin)
        self.assertEqual(self.client.post("/employee/leaves", data={}).status_code, 403)
        self.assertEqual(self.client.get("/admin/leaves?status=invalid").status_code, 400)
        self.assertEqual(self.client.post("/admin/leaves/999/review", data={"decision": "approved"}).status_code, 404)
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.client.post(
            f"/admin/leaves/{leave.id}/review", data={"decision": "approved"},
        ).status_code, 400)

    def test_today_excludes_future_past_pending_and_inactive_employees(self):
        self.add_leave(start_date=self.today + timedelta(days=1),
                       end_date=self.today + timedelta(days=1), status="approved")
        self.add_leave(start_date=self.today - timedelta(days=1),
                       end_date=self.today - timedelta(days=1), status="approved")
        self.add_leave()
        self.other.is_active = False
        self.add_leave(employee=self.other, status="approved")
        self.login(self.admin)
        data = self.client.get("/api/admin/live-status").json
        self.assertEqual(data["on_leave_count"], 0)
        self.assertEqual(data["pending_leave_count"], 1)
        self.assertIn(b"No leave requests found", self.client.get("/admin/leaves?status=today").data)

    def test_expired_and_inactive_requests_cannot_be_approved(self):
        expired = self.add_leave(start_date=self.today - timedelta(days=1),
                                 end_date=self.today - timedelta(days=1))
        inactive = self.add_leave(employee=self.other)
        self.other.is_active = False
        db.session.commit()
        self.login(self.admin)
        for leave in (expired, inactive):
            response = self.client.post(f"/admin/leaves/{leave.id}/review",
                                        data={"decision": "approved"}, follow_redirects=True)
            self.assertIn(b"cannot be approved", response.data)
            db.session.refresh(leave)
            self.assertEqual(leave.status, "pending")

    def test_employee_with_leave_history_is_not_deleted(self):
        self.add_leave()
        self.login(self.admin)
        response = self.client.post(f"/admin/employees/{self.employee.id}/delete", follow_redirects=True)
        self.assertIn(b"deactivated instead of deleted", response.data)
        self.assertIsNotNone(db.session.get(User, self.employee.id))
        self.assertEqual(LeaveRequest.query.count(), 1)

    def test_leave_does_not_hide_active_site_visit(self):
        self.add_leave(status="approved")
        site = AssignedSite(site_name="Test site",
                            customer_name="Customer", address="Test address",
                            latitude=0, longitude=0, allowed_radius=100)
        db.session.add(site)
        db.session.flush()
        db.session.add(Attendance(employee_id=self.employee.id, site_id=site.id,
                                  checkin_lat=0, checkin_lon=0, checkin_selfie="test.png"))
        db.session.commit()
        self.login(self.admin)
        data = self.client.get("/api/admin/live-status").json
        self.assertEqual(data["on_leave_count"], 1)
        self.assertEqual(data["onsite_count"], 1)
        self.assertEqual(next(row for row in data["employees"] if row["name"] == "Engineer")["status"], "onsite")

    def assert_total(self, response, total):
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'id="leave-total">{total}</div>'.encode(), response.data)

    def test_date_range_totals_clip_requests_and_count_half_days(self):
        self.add_leave(
            start_date=self.today - timedelta(days=10),
            end_date=self.today - timedelta(days=2), status="approved",
            reason="Approved long leave",
        )
        self.add_leave(period="morning", status="approved", reason="Morning appointment")
        self.add_leave(period="afternoon", status="approved")
        self.add_leave(status="pending", reason="Pending excluded from total")
        self.add_leave(status="rejected", reason="Rejected excluded from total")
        self.add_leave(start_date=self.today + timedelta(days=1),
                       end_date=self.today + timedelta(days=3), status="approved")
        self.login(self.employee)
        self.assert_total(self.client.get("/employee/leaves"), 10)
        response = self.client.get("/employee/leaves", query_string={
            "from_date": (self.today - timedelta(days=5)).isoformat(),
            "to_date": (self.today - timedelta(days=3)).isoformat(),
        })
        self.assert_total(response, 3)
        self.assertIn(b"Approved long leave", response.data)
        self.assertNotIn(b"Morning appointment", response.data)
        self.assert_total(self.client.get("/employee/leaves", query_string={
            "from_date": self.today.isoformat(), "to_date": self.today.isoformat(),
        }), 1)
        self.assert_total(self.client.get("/employee/leaves", query_string={
            "from_date": (self.today + timedelta(days=1)).isoformat(),
        }), 0)
        self.assert_total(self.client.get("/employee/leaves", query_string={
            "to_date": (self.today - timedelta(days=5)).isoformat(),
        }), 6)

    def test_ongoing_leave_counts_only_through_today(self):
        self.add_leave(start_date=self.today - timedelta(days=1),
                       end_date=self.today + timedelta(days=2), status="approved")
        self.login(self.employee)
        self.assert_total(self.client.get("/employee/leaves"), 2)
        self.assert_total(self.client.get("/employee/leaves", query_string={
            "from_date": self.today.isoformat(),
            "to_date": (self.today + timedelta(days=5)).isoformat(),
        }), 1)

    def test_admin_search_selection_and_status_preserve_totals(self):
        self.add_leave(status="approved", period="morning", reason="Engineer approved")
        self.add_leave(employee=self.other, status="approved", reason="Other approved")
        self.login(self.admin)
        self.assert_total(self.client.get("/admin/leaves"), 1.5)
        response = self.client.get("/admin/leaves", query_string={
            "employee_search": "eNGin", "employee_id": self.employee.id,
            "from_date": self.today.isoformat(), "status": "pending",
        })
        self.assert_total(response, 0.5)
        self.assertNotIn(b"Engineer approved", response.data)
        self.assertNotIn(b"Other approved", response.data)
        self.assertIn(b"employee_search=eNGin", response.data)
        self.assertIn(f"employee_id={self.employee.id}".encode(), response.data)
        response = self.client.get("/admin/leaves", query_string={
            "employee_search": self.other.email.upper(),
        })
        self.assert_total(response, 1)
        self.assertIn(b"Other approved", response.data)
        self.assertNotIn(b"Engineer approved", response.data)
        for search in ("missing employee", "%", "_"):
            with self.subTest(search=search):
                self.assert_total(self.client.get("/admin/leaves", query_string={
                    "employee_search": search,
                }), 0)
        self.other.is_active = False
        db.session.commit()
        self.assert_total(self.client.get("/admin/leaves", query_string={
            "employee_id": self.other.id,
        }), 1)

    def test_employee_cannot_change_totals_or_history_owner_with_filters(self):
        self.add_leave(status="approved", period="morning", reason="My approved reason")
        self.add_leave(employee=self.other, status="approved", reason="Other private reason")
        self.login(self.employee)
        response = self.client.get("/employee/leaves", query_string={
            "employee_id": self.other.id, "employee_search": self.other.name,
            "status": "all",
        })
        self.assert_total(response, 0.5)
        self.assertIn(b"My approved reason", response.data)
        self.assertNotIn(b"Other private reason", response.data)
        self.assertNotIn(b'id="employee-search"', response.data)
        self.assertNotIn(self.other.email.encode(), response.data)

    def test_invalid_date_and_employee_filters_report_errors(self):
        self.login(self.admin)
        for filters in (
            {"from_date": "bad"}, {"to_date": "2026-02-30"},
            {"from_date": "2026-02-02", "to_date": "2026-02-01"},
            {"employee_id": "invalid"}, {"employee_id": "999"},
            {"employee_id": str(self.admin.id)},
        ):
            with self.subTest(filters=filters):
                self.assertEqual(self.client.get("/admin/leaves", query_string=filters).status_code, 400)
        self.login(self.employee)
        self.assertEqual(self.client.get("/employee/leaves?from_date=bad").status_code, 400)
