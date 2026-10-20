import base64
import io
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
from datetime import date, datetime, timedelta, timezone

from PIL import Image
from pypdf import PdfReader
from openpyxl import load_workbook

from geosite import create_app
from geosite.extensions import db
from geosite.models import AssignedSite, Attendance, AuditLog, Invoice, LeaveRequest, SiteReport, User


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

    def test_activity_excel_layout_billing_leave_and_missing_dates(self):
        start = date(2026, 10, 19)
        self.site.project_name = "=Project text"
        self.site.po_number = "PO-100"
        self.site.po_date = start
        self.site.report_company = "Example Reporting Company"
        self.site.commissioning_manager = "Commissioning Lead"
        self.site.project_manager = "Project Lead"
        self.site.report_remarks = "First remark\n=literal remark"
        for billing, detail in ((True, "Billable work"), (False, "Non-billable meeting"),
                                (True, "=literal activity")):
            visit = Attendance(
                employee_id=self.employee.id, site_id=self.site.id,
                status="completed", checkin_lat=0, checkin_lon=0,
                checkin_selfie="selfie.png",
                checkout_time=datetime(2026, 10, 19, 12, tzinfo=timezone.utc),
            )
            db.session.add(visit)
            db.session.flush()
            db.session.add(SiteReport(
                attendance=visit, work_performed=detail, is_billable=billing,
                activities_completed="", issues_found="", recommendations="",
                materials_used="", remarks="", engineer_name="Engineer",
                customer_representative_name="Customer", employee_signature="a.png",
                customer_signature="b.png",
            ))
        db.session.add(LeaveRequest(
            employee_id=self.employee.id, start_date=start + timedelta(days=1),
            end_date=start + timedelta(days=1), period="full", reason="Sick Leave",
            status="approved",
        ))
        db.session.add(LeaveRequest(
            employee_id=self.employee.id, start_date=start + timedelta(days=2),
            end_date=start + timedelta(days=2), period="full", reason="Unapproved reason",
            status="pending",
        ))
        db.session.commit()
        self._login_employee()
        response = self.client.get("/reports/activity.xlsx", query_string={
            "site_id": self.site.id, "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=6)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(io.BytesIO(response.data)).active
        self.assertEqual(sheet.title, "SAR-R0")
        self.assertEqual(sheet["A1"].value, "SITE ACTIVITY REPORT")
        self.assertEqual(sheet["A3"].value, "PROJECT : =Project text")
        self.assertEqual(sheet["A5"].value, "PO NO : PO-100")
        self.assertIn("E8:L8", str(sheet.merged_cells))
        self.assertEqual([sheet.cell(8, c).value for c in range(1, 6)],
                         ["SR NO", "SITE", "DATE", "DAY", "ACTIVITY DETAIL"])
        self.assertEqual([sheet.cell(r, 4).value for r in range(9, 13)], [1, 0, 1, 0])
        self.assertEqual(sheet["E11"].value, "=literal activity")
        self.assertEqual(sheet["E11"].data_type, "s")
        self.assertEqual(sheet["E12"].value, "Sick Leave")
        self.assertEqual(sheet["E13"].value, "No activity recorded")
        self.assertEqual(sheet["E17"].value, "Sunday")
        self.assertEqual(sheet["K24"].value, 2)
        self.assertEqual(sheet["A18"].value, "REMARKS")
        self.assertEqual([sheet.cell(r, 1).value for r in range(19, 24)], [1, 2, 3, 4, 5])
        self.assertEqual(sheet["C19"].value, "First remark")
        self.assertEqual(sheet["C20"].value, "=literal remark")
        self.assertEqual(sheet["C20"].data_type, "s")
        self.assertIsNone(sheet["C21"].value)
        self.assertEqual(sheet["I25"].value, "M/S Example Reporting Company")
        self.assertIn("Commissioning Manager : Commissioning Lead", sheet["A26"].value)
        self.assertIn("Customer representative:", sheet["A26"].value)
        self.assertIn("Project Manager : Project Lead", sheet["E26"].value)
        self.assertIn("Engineer : Engineer", sheet["I26"].value)
        for merge in ("A26:D30", "E26:H30", "I26:L30", "C19:L19"):
            self.assertIn(merge, str(sheet.merged_cells))
        self.assertEqual(sheet.page_setup.orientation, "portrait")
        self.assertEqual(sheet["A8"].fill.fgColor.rgb, "003A3838")
        self.assertNotIn("Unapproved reason", [cell.value for row in sheet for cell in row])
        self.assertEqual(sheet.freeze_panes, "E9")

    def test_activity_excel_enforces_owner_and_valid_date_range(self):
        other = User(name="Other", email="other@example.com", mobile="3")
        other.set_password("other-password-123")
        db.session.add(other)
        db.session.flush()
        site = AssignedSite(employee_id=other.id, site_name="Private Site",
                            customer_name="Customer", address="Address",
                            latitude=0, longitude=0)
        db.session.add(site)
        db.session.commit()
        self._login_employee()
        params = {"site_id": site.id, "start_date": "2026-10-19", "end_date": "2026-10-20"}
        self.assertEqual(self.client.get("/reports/activity.xlsx", query_string=params).status_code, 404)
        self.assertNotIn(b"Private Site", self.client.get("/reports").data)
        for updates in ({"start_date": "bad"}, {"end_date": "2026-10-01"},
                        {"end_date": "2028-10-20"}, {"site_id": "bad"}):
            response = self.client.get("/reports/activity.xlsx", query_string={**params, **updates},
                                       follow_redirects=True)
            self.assertIn(b"Select a site and valid", response.data)
        self.client.post("/logout")
        self.client.post("/login", data={"email": self.admin.email, "password": "admin-password-123"})
        params["employee_id"] = other.id
        self.assertEqual(self.client.get("/reports/activity.xlsx", query_string=params).status_code, 200)

    def test_admin_and_employee_excel_use_identical_layout_and_data(self):
        params = {"site_id": self.site.id, "start_date": "2026-10-19", "end_date": "2026-10-21"}
        self._login_employee()
        employee_response = self.client.get("/reports/activity.xlsx", query_string=params)
        employee_sheet = load_workbook(io.BytesIO(employee_response.data)).active
        self.assertNotIn(b'id="activity-employee"', self.client.get("/reports").data)
        self.client.post("/logout")
        self.client.post("/login", data={"email": self.admin.email, "password": "admin-password-123"})
        self.assertIn(b'id="activity-employee"', self.client.get("/reports").data)
        response = self.client.get("/admin/reports/export.xlsx", query_string=params, follow_redirects=True)
        self.assertIn(b"Select an employee", response.data)
        params["employee_id"] = self.employee.id
        for route in ("/reports/activity.xlsx", "/admin/reports/export.xlsx"):
            response = self.client.get(route, query_string=params)
            self.assertEqual(response.status_code, 200)
            admin_sheet = load_workbook(io.BytesIO(response.data)).active
            self.assertEqual(list(admin_sheet.values), list(employee_sheet.values))
            self.assertEqual(str(admin_sheet.merged_cells), str(employee_sheet.merged_cells))
            for row in employee_sheet:
                for cell in row:
                    self.assertEqual(admin_sheet[cell.coordinate].style_id, cell.style_id)
        params["employee_id"] = self.admin.id
        self.assertEqual(self.client.get("/reports/activity.xlsx", query_string=params).status_code, 404)

    def test_admin_project_header_changes(self):
        self._login_employee()
        url = f"/admin/sites/{self.site.id}/report-details"
        self.assertEqual(self.client.post(url, data={}).status_code, 403)
        self.client.post("/logout")
        self.client.post("/login", data={"email": self.admin.email, "password": "admin-password-123"})
        self.client.post(url, data={"project_name": "Commissioning", "po_number": "PO-42",
                                   "po_date": "2026-10-01", "report_company": "Company",
                                   "commissioning_manager": "Manager One",
                                   "project_manager": "Manager Two", "report_remarks": "One\nTwo"})
        db.session.refresh(self.site)
        self.assertEqual(self.site.project_name, "Commissioning")
        self.assertEqual(self.site.po_number, "PO-42")
        self.assertEqual(self.site.po_date, date(2026, 10, 1))
        self.assertEqual(self.site.report_company, "Company")
        self.assertEqual(self.site.commissioning_manager, "Manager One")
        self.assertEqual(self.site.project_manager, "Manager Two")
        self.assertEqual(self.site.report_remarks, "One\nTwo")
        response = self.client.post(url, data={"po_date": "bad"}, follow_redirects=True)
        self.assertIn(b"Enter a valid PO date", response.data)
        self.assertEqual(self.site.po_number, "PO-42")
        for values in ({"report_remarks": "Line\n" * 6}, {"report_company": "x" * 161},
                       {"project_manager": "x" * 121}, {"commissioning_manager": "x" * 121}):
            response = self.client.post(url, data=values, follow_redirects=True)
            self.assertIn(b"at most five remarks lines", response.data)
            db.session.refresh(self.site)
            self.assertEqual(self.site.report_company, "Company")

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
            "activity_detail": "Inspection and functional checks",
            "billing": "billable",
            "work_performed": "Inspection",
            "activities_completed": "Functional checks",
            "issues_found": "None",
            "recommendations": "Continue monthly checks",
            "materials_used": "None",
            "remarks": "All checks passed",
            "engineer_name": "Engineer",
        }
        for billing in ("", "invalid"):
            response = self.client.post(
                f"/visit/{attendance.id}/report", data={**payload, "billing": billing}
            )
            self.assertIn(b"select Billable or Non-billable", response.data)
            self.assertEqual(SiteReport.query.count(), 0)
        response = self.client.post(
            f"/visit/{attendance.id}/report", data={**payload, "activity_detail": ""}
        )
        self.assertIn(b"Enter activity detail", response.data)
        for name in ("work_performed", "activities_completed", "issues_found",
                     "recommendations", "materials_used", "remarks"):
            payload.pop(name)
        form = self.client.get(f"/visit/{attendance.id}/report")
        self.assertNotIn(b'name="customer_representative_name"', form.data)
        self.assertNotIn(b'class="signature-pad"', form.data)
        self.assertNotIn(b'name="employee_signature"', form.data)
        self.assertNotIn(b'name="customer_signature"', form.data)
        response = self.client.post(
            f"/visit/{attendance.id}/report", data=payload, follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(attendance)
        self.assertEqual(attendance.status, "completed")
        self.assertIsNotNone(SiteReport.query.one())
        self.assertTrue(SiteReport.query.one().is_billable)
        self.assertEqual(SiteReport.query.one().work_performed, "Inspection and functional checks")
        self.assertEqual(SiteReport.query.one().customer_representative_name, "")
        self.assertEqual(SiteReport.query.one().employee_signature, "")
        self.assertEqual(SiteReport.query.one().customer_signature, "")

        single = self.client.get(f"/reports/{attendance.id}/pdf")
        pdf = PdfReader(io.BytesIO(single.data))
        page_count = len(pdf.pages)
        pdf_text = "\n".join(page.extract_text() for page in pdf.pages)
        for label in ("Customer Representative", "Engineer Signature", "Customer Signature",
                      "Offline Approval", "Date:"):
            self.assertIn(label, pdf_text)
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

    def test_checkout_requires_activity_billing_and_engineer_name(self):
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
        response = self.client.post(f"/visit/{attendance.id}/report", data={
            "activity_detail": "Inspection", "billing": "non_billable", "engineer_name": "",
        })
        self.assertIn(b"enter the engineer name", response.data)
        self.assertEqual(SiteReport.query.count(), 0)
        self.client.post(f"/visit/{attendance.id}/report", data={
            "activity_detail": "Inspection", "billing": "non_billable", "engineer_name": "Engineer",
        })
        self.assertEqual(Attendance.query.one().status, "completed")
        self.assertFalse(SiteReport.query.one().is_billable)

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
        self.assertEqual(result.status_code, 302)
        self.assertTrue(result.location.endswith("/reports#activity-export"))
        result = self.client.get("/admin/reports/export.xlsx", query_string={
            "employee_id": self.employee.id, "site_id": self.site.id,
            "start_date": "2026-10-19", "end_date": "2026-10-20",
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(load_workbook(io.BytesIO(result.data)).active.title, "SAR-R0")

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
