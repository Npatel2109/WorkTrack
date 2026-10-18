import base64
import io
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from PIL import Image
from pypdf import PdfReader

from geosite import create_app
from geosite.extensions import db
from geosite.models import AssignedSite, Attendance, AuditLog, Invoice, SiteReport, User


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "UPLOAD_FOLDER": self.uploads.name,
                "WTF_CSRF_ENABLED": False,
            }
        )
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.admin = User(
            name="Admin",
            email="admin@example.com",
            mobile="100",
            designation="Administrator",
            role="admin",
        )
        self.admin.set_password("admin-password-123")
        self.employee = User(
            name="Engineer",
            email="engineer@example.com",
            mobile="200",
            designation="Field Engineer",
        )
        self.employee.set_password("engineer-password-123")
        db.session.add_all([self.admin, self.employee])
        db.session.flush()
        self.site = AssignedSite(
            employee_id=self.employee.id,
            site_name="North Site",
            customer_name="Example Customer",
            address="1 Example Road",
            latitude=0,
            longitude=0,
            allowed_radius=100,
        )
        db.session.add(self.site)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.uploads.cleanup()

    @staticmethod
    def _image_data():
        buffer = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    def _login_employee(self):
        return self.client.post(
            "/login",
            data={"email": self.employee.email, "password": "engineer-password-123"},
            follow_redirects=True,
        )

    def test_employee_cannot_open_admin_page(self):
        self._login_employee()
        self.assertEqual(self.client.get("/admin").status_code, 403)

    def test_end_to_end_visit_and_merged_report_pdf(self):
        self._login_employee()
        response = self.client.post(f"/visit/start/{self.site.id}")
        self.assertEqual(response.status_code, 302)
        too_far = self.client.post(
            f"/api/visit/{self.site.id}/verify-checkin",
            json={"latitude": 0, "longitude": 0.01},
        )
        self.assertFalse(too_far.get_json()["verified"])
        self.assertEqual(self.client.get(f"/visit/{self.site.id}/camera").status_code, 403)
        verified = self.client.post(
            f"/api/visit/{self.site.id}/verify-checkin",
            json={"latitude": 0, "longitude": 0},
        )
        self.assertTrue(verified.get_json()["verified"])
        result = self.client.post(
            f"/visit/{self.site.id}/checkin",
            data={"selfie": self._image_data()},
            follow_redirects=True,
        )
        self.assertEqual(result.status_code, 200)
        attendance = Attendance.query.one()
        self.assertEqual(attendance.status, "onsite")
        self.assertIn("/employee", self.client.post("/logout").location)

        checkout = self.client.post(
            f"/api/visit/{attendance.id}/verify-checkout",
            json={"latitude": 0, "longitude": 0},
        )
        self.assertTrue(checkout.get_json()["verified"])
        payload = {
            "work_performed": "Inspection",
            "activities_completed": "Functional checks",
            "issues_found": "None",
            "recommendations": "Continue monthly checks",
            "materials_used": "None",
            "remarks": "All checks passed",
            "engineer_name": "Engineer",
            "customer_representative_name": "Customer Rep",
            "employee_signature": self._image_data(),
            "customer_signature": self._image_data(),
        }
        response = self.client.post(
            f"/visit/{attendance.id}/report", data=payload, follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(attendance)
        self.assertEqual(attendance.status, "completed")
        self.assertIsNotNone(SiteReport.query.one())

        single = self.client.get(f"/reports/{attendance.id}/pdf")
        page_count = len(PdfReader(io.BytesIO(single.data)).pages)
        single.close()
        combined = self.client.get(
            "/reports/combined.pdf",
            query_string={
                "start_date": attendance.checkout_time.date().isoformat(),
                "end_date": attendance.checkout_time.date().isoformat(),
            },
        )
        self.assertEqual(combined.status_code, 200)
        self.assertEqual(len(PdfReader(io.BytesIO(combined.data)).pages), page_count)

    def test_checkout_requires_all_fields_and_signatures(self):
        self._login_employee()
        self.client.post(
            f"/api/visit/{self.site.id}/verify-checkin",
            json={"latitude": 0, "longitude": 0},
        )
        self.client.post(
            f"/visit/{self.site.id}/checkin", data={"selfie": self._image_data()}
        )
        attendance = Attendance.query.one()
        self.client.post(
            f"/api/visit/{attendance.id}/verify-checkout",
            json={"latitude": 0, "longitude": 0},
        )
        self.client.post(f"/visit/{attendance.id}/report", data={"work_performed": "Inspection"})
        self.assertEqual(Attendance.query.one().status, "onsite")
        self.assertEqual(SiteReport.query.count(), 0)

    def test_admin_invoices_use_days_times_daily_rate(self):
        self.client.post(
            "/login",
            data={"email": self.admin.email, "password": "admin-password-123"},
        )
        response = self.client.post(
            "/admin/invoices",
            data={
                "site_id": self.site.id,
                "customer_name": "Example Customer",
                "description": "Engineering service",
                "start_date": "2026-10-10",
                "end_date": "2026-10-12",
                "billable_days": "2",
                "daily_rate": "123.45",
                "currency": "INR",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        invoice = Invoice.query.one()
        self.assertEqual(str(invoice.total_amount), "246.90")
        pdf = self.client.get(f"/admin/invoices/{invoice.id}/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(len(PdfReader(io.BytesIO(pdf.data)).pages), 1)

    def test_dashboard_cards_open_their_detail_pages(self):
        self.client.post(
            "/login",
            data={"email": self.admin.email, "password": "admin-password-123"},
        )
        dashboard = self.client.get("/admin")
        self.assertIn(b"/admin/metrics/employees", dashboard.data)
        for metric in ("employees", "sites", "onsite", "completed", "reports"):
            with self.subTest(metric=metric):
                self.assertEqual(self.client.get(f"/admin/metrics/{metric}").status_code, 200)

    def test_admin_pages_render(self):
        self.client.post(
            "/login",
            data={"email": self.admin.email, "password": "admin-password-123"},
        )
        for path in (
            "/admin",
            "/admin/employees",
            "/admin/sites",
            "/admin/invoices",
            "/reports",
            "/admin/attendance",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_admin_live_status_and_excel(self):
        self.client.post(
            "/login",
            data={"email": self.admin.email, "password": "admin-password-123"},
        )
        self.assertEqual(
            self.client.get("/api/admin/live-status").get_json()["employees"][0]["status"],
            "offline",
        )
        db.session.add(AuditLog(user_id=self.employee.id, action="login"))
        db.session.commit()
        self.assertEqual(
            self.client.get("/api/admin/live-status").get_json()["employees"][0]["status"],
            "assigned",
        )
        result = self.client.get("/admin/reports/export.xlsx")
        self.assertEqual(result.status_code, 200)
        self.assertIn("spreadsheetml", result.mimetype)

    def test_password_reset_expires_after_use(self):
        with patch("geosite.routes.send_password_reset_email") as send_email:
            response = self.client.post(
                "/forgot-password",
                data={"email": self.employee.email},
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        reset_url = send_email.call_args.args[2]
        token = urlparse(reset_url).path.rsplit("/", 1)[1]
        self.assertEqual(self.client.get(f"/reset-password/{token}").status_code, 200)
        result = self.client.post(
            f"/reset-password/{token}",
            data={
                "password": "employee-new-password-456",
                "confirm_password": "employee-new-password-456",
            },
            follow_redirects=True,
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get(f"/reset-password/{token}").status_code, 302)
        login = self.client.post(
            "/login",
            data={
                "email": self.employee.email,
                "password": "employee-new-password-456",
            },
        )
        self.assertEqual(login.status_code, 302)


if __name__ == "__main__":
    unittest.main()
