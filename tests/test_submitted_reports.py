import io
import tempfile
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

from flask_migrate import downgrade, upgrade
from pypdf import PdfWriter
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from geosite import create_app
from geosite.extensions import db
from geosite.models import (
    AssignedSite, AuditLog, SiteAssignment, SubmittedReport,
    SubmittedReportVersion, User,
)


class SubmittedReportTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "submitted-report-tests",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "UPLOAD_FOLDER": self.uploads.name, "WTF_CSRF_ENABLED": False,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.employee = self.make_user("Engineer")
        self.other = self.make_user("Other")
        self.admin = self.make_user("Admin", role="admin")
        self.site = AssignedSite(
            site_name="North Site", customer_name="Example Customer",
            address="1 Example Road", latitude=19, longitude=72, allowed_radius=100,
        )
        db.session.add(self.site)
        db.session.flush()
        db.session.add(SiteAssignment(employee=self.employee, site=self.site))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.uploads.cleanup()

    def make_user(self, name, role="employee"):
        user = User(name=name, email=f"{name.lower()}@example.com", mobile="100", role=role)
        user.set_password("report-password-123")
        db.session.add(user)
        db.session.flush()
        return user

    def login(self, user):
        self.client.post("/logout")
        self.client.post("/login", data={"email": user.email, "password": "report-password-123"})

    @staticmethod
    def pdf_bytes(width=100, encrypted=False, empty=False):
        writer = PdfWriter()
        if not empty:
            writer.add_blank_page(width=width, height=100)
        if encrypted:
            writer.encrypt("secret")
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def submit(self, **overrides):
        data = {
            "site_id": str(self.site.id),
            "start_date": "2026-10-01", "end_date": "2026-10-10",
            "total_mandays": "8",
            "pdf": (io.BytesIO(self.pdf_bytes()), "report.pdf"),
        }
        data.update(overrides)
        return self.client.post("/employee/submitted-reports/new", data=data)

    def edit(self, report, **overrides):
        current = report.current_version
        data = {
            "site_id": str(report.site_id or self.site.id),
            "start_date": current.start_date.isoformat(), "end_date": current.end_date.isoformat(),
            "total_mandays": str(current.total_mandays), "revision_number": str(report.revision_number),
        }
        data.update(overrides)
        return self.client.post(f"/employee/submitted-reports/{report.id}/edit", data=data)

    @staticmethod
    def download_url(report, version):
        return f"/submitted-reports/{report.id}/versions/{version.id}/download"

    def test_submit_assigned_site_and_download_by_owner_and_admin(self):
        self.login(self.employee)
        self.assertIn(b"Submitted reports", self.client.get("/employee").data)
        form = self.client.get("/employee/submitted-reports/new")
        self.assertIn(b"Submit report", form.data)
        self.assertIn(b"North Site - Example Customer", form.data)
        response = self.submit(employee_id=str(self.other.id), total_mandays="12")
        self.assertEqual(response.status_code, 302)
        report = SubmittedReport.query.one()
        current = report.current_version
        self.assertEqual(report.employee_id, self.employee.id)
        self.assertEqual(report.site_id, self.site.id)
        self.assertEqual(current.total_mandays, 12)
        self.assertEqual(current.revision_number, 1)
        self.assertTrue(current.pdf_replaced)
        self.assertEqual(current.start_date, date(2026, 10, 1))
        self.assertEqual(Path(self.uploads.name, current.filename).read_bytes(), self.pdf_bytes())
        self.assertIsNotNone(AuditLog.query.filter_by(action="report_submitted").first())
        for user in (self.employee, self.admin):
            self.login(user)
            self.assertIn(b"12", self.client.get("/submitted-reports").data)
            detail = self.client.get(f"/submitted-reports/{report.id}")
            self.assertIn(b"Submission and replacement history", detail.data)
            self.assertIn(b"Engineer", detail.data)
            download = self.client.get(self.download_url(report, current))
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.mimetype, "application/pdf")
            self.assertEqual(download.data, self.pdf_bytes())
            self.assertIn("attachment", download.headers["Content-Disposition"])
            self.assertEqual(download.headers["Cache-Control"], "private, no-store")
            download.close()
        self.login(self.admin)
        deletion = self.client.post(
            f"/admin/sites/{self.site.id}/delete", follow_redirects=True
        )
        self.assertIn(b"submitted-report history and cannot be deleted", deletion.data)
        self.assertIsNotNone(db.session.get(AssignedSite, self.site.id))

    def test_submission_requires_an_active_site_assigned_to_the_employee(self):
        self.login(self.employee)
        unassigned_site = AssignedSite(
            site_name="Other Site", customer_name="Other Customer",
            address="2 Example Road", latitude=20, longitude=73, allowed_radius=100,
        )
        db.session.add(unassigned_site)
        db.session.commit()
        missing = self.submit(site_id="")
        self.assertEqual(missing.status_code, 400)
        self.assertIn(b"Select an active site assigned to you.", missing.data)
        unassigned = self.submit(site_id=str(unassigned_site.id))
        self.assertEqual(unassigned.status_code, 400)
        self.assertEqual(SubmittedReport.query.count(), 0)

    def test_edit_can_change_site_and_preserves_a_historical_unassigned_site(self):
        self.login(self.employee)
        self.submit()
        report = SubmittedReport.query.one()
        second_site = AssignedSite(
            site_name="South Site", customer_name="Example Customer",
            address="3 Example Road", latitude=21, longitude=74, allowed_radius=100,
        )
        db.session.add(second_site)
        db.session.flush()
        db.session.add(SiteAssignment(employee=self.employee, site=second_site))
        db.session.commit()
        response = self.edit(report, site_id=str(second_site.id), total_mandays="9")
        self.assertEqual(response.status_code, 302)
        db.session.refresh(report)
        self.assertEqual(report.site_id, second_site.id)
        self.assertEqual(report.revision_number, 2)

        db.session.delete(report.site.assignments[0])
        db.session.commit()
        form = self.client.get(f"/employee/submitted-reports/{report.id}/edit")
        self.assertEqual(form.status_code, 200)
        self.assertIn(b"South Site - Example Customer (no longer assigned)", form.data)
        self.assertEqual(self.edit(report, total_mandays="10").status_code, 302)
        self.assertEqual(report.site_id, second_site.id)

    def test_replace_pdf_and_edit_metadata_retains_downloadable_history(self):
        self.login(self.employee)
        self.submit()
        report = SubmittedReport.query.one()
        original = report.current_version
        original_filename = original.filename
        response = self.edit(
            report, start_date="2026-10-02", end_date="2026-10-12", total_mandays="9",
            pdf=(io.BytesIO(self.pdf_bytes(width=200)), "replaced.PDF"),
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(report)
        self.assertEqual(report.revision_number, 2)
        self.assertEqual(len(report.versions), 2)
        latest = report.current_version
        self.assertNotEqual(latest.filename, original_filename)
        self.assertEqual(original.total_mandays, 8)
        self.assertEqual(latest.total_mandays, 9)
        self.assertEqual(latest.start_date, date(2026, 10, 2))
        self.assertIsNotNone(AuditLog.query.filter_by(action="report_pdf_replaced").first())
        for user in (self.employee, self.admin):
            self.login(user)
            history = self.client.get(f"/submitted-reports/{report.id}")
            self.assertIn(b"PDF replaced / details saved", history.data)
            self.assertIn(b"report.pdf", history.data)
            self.assertIn(b"replaced.PDF", history.data)
            for version, contents in ((original, self.pdf_bytes()), (latest, self.pdf_bytes(width=200))):
                download = self.client.get(self.download_url(report, version))
                self.assertEqual(download.data, contents)
                download.close()
        self.assertEqual(len(list(Path(self.uploads.name).iterdir())), 2)

    def test_metadata_only_edit_preserves_pdf_and_noop_does_not_create_version(self):
        self.login(self.employee)
        self.submit()
        report = SubmittedReport.query.one()
        old_filename = report.current_version.filename
        form = self.client.get(f"/employee/submitted-reports/{report.id}/edit")
        self.assertIn(b'value="2026-10-01"', form.data)
        self.assertIn(b'Replacement PDF (optional)', form.data)
        self.assertEqual(self.edit(report, total_mandays="4").status_code, 302)
        db.session.refresh(report)
        self.assertEqual(report.revision_number, 2)
        self.assertEqual(report.current_version.filename, old_filename)
        self.assertFalse(report.current_version.pdf_replaced)
        self.assertEqual(report.current_version.total_mandays, 4)
        self.assertIsNotNone(AuditLog.query.filter_by(action="report_details_updated").first())
        self.assertEqual(self.edit(report).status_code, 302)
        self.assertEqual(SubmittedReportVersion.query.count(), 2)
        self.assertEqual(len(list(Path(self.uploads.name).iterdir())), 1)

    def test_employee_cannot_access_another_employees_reports_or_versions(self):
        self.login(self.employee)
        self.submit()
        first = SubmittedReport.query.one()
        first_id = first.id
        version_id = first.current_version.id
        self.login(self.other)
        listing = self.client.get("/submitted-reports")
        self.assertIn(b"No submitted reports yet", listing.data)
        for url in (
            f"/submitted-reports/{first_id}",
            f"/employee/submitted-reports/{first_id}/edit",
            f"/submitted-reports/{first_id}/versions/{version_id}/download",
        ):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.edit(first, total_mandays="1").status_code, 404)
        db.session.add(SiteAssignment(employee=self.other, site=self.site))
        db.session.commit()
        self.submit()
        second = SubmittedReport.query.filter_by(employee_id=self.other.id).one()
        self.assertEqual(self.client.get(
            f"/submitted-reports/{second.id}/versions/{version_id}/download"
        ).status_code, 404)
        self.login(self.admin)
        listing = self.client.get("/submitted-reports")
        self.assertIn(b"Engineer", listing.data)
        self.assertIn(b"Other", listing.data)
        self.assertNotIn(b"Submit report</a>", listing.data)
        for report in (first, second):
            self.assertEqual(self.client.get(f"/submitted-reports/{report.id}").status_code, 200)
            self.assertEqual(self.client.get(f"/employee/submitted-reports/{report.id}/edit").status_code, 403)
            self.assertEqual(self.edit(report).status_code, 403)
        self.assertEqual(self.client.get("/employee/submitted-reports/new").status_code, 403)
        self.assertEqual(self.submit().status_code, 403)

    def test_authentication_and_csrf_required(self):
        for url in (
            "/submitted-reports", "/submitted-reports/1", "/employee/submitted-reports/new",
            "/employee/submitted-reports/1/edit", "/submitted-reports/1/versions/1/download",
        ):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.submit().status_code, 302)
        self.login(self.employee)
        self.submit()
        report = SubmittedReport.query.one()
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.submit().status_code, 400)
        self.assertEqual(self.edit(report, total_mandays="1").status_code, 400)
        self.assertEqual(SubmittedReportVersion.query.count(), 1)

    def test_invalid_dates_and_non_whole_mandays_preserve_form_without_saving(self):
        self.login(self.employee)
        invalid = (
            {"start_date": ""}, {"start_date": "not-a-date"}, {"end_date": "2026-09-01"},
            {"end_date": ""}, {"total_mandays": ""}, {"total_mandays": "0"},
            {"total_mandays": "-1"}, {"total_mandays": "1.5"}, {"total_mandays": "2.0"},
            {"total_mandays": "nan"}, {"total_mandays": "2147483648"},
        )
        for values in invalid:
            with self.subTest(values=values):
                response = self.submit(**values)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Invalid report details", response.data)
        response = self.submit(total_mandays="1.5")
        self.assertIn(b'value="1.5"', response.data)
        self.assertEqual(SubmittedReport.query.count(), 0)
        self.assertEqual(list(Path(self.uploads.name).iterdir()), [])

    def test_rejects_missing_renamed_corrupt_empty_encrypted_and_pageless_pdfs(self):
        self.login(self.employee)
        invalid = (
            None, (b"", "empty.pdf"), (b"not a PDF", "renamed.pdf"),
            (b"%PDF-1.7\nbroken\n%%EOF", "broken.pdf"),
            (self.pdf_bytes(), "report.txt"),
            (self.pdf_bytes(encrypted=True), "encrypted.pdf"),
            (self.pdf_bytes(empty=True), "pageless.pdf"),
        )
        for payload in invalid:
            with self.subTest(payload=payload[1] if payload else None):
                upload = (io.BytesIO(payload[0]), payload[1]) if payload else None
                response = self.submit(pdf=upload)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(SubmittedReport.query.count(), 0)
        self.assertEqual(list(Path(self.uploads.name).iterdir()), [])

    def test_pdf_size_limit_and_sanitized_filename(self):
        self.login(self.employee)
        raw = self.pdf_bytes()
        with patch("geosite.services.MAX_REPORT_PDF_BYTES", len(raw) - 1):
            response = self.submit()
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"exceeds the 5 MB", response.data)
        self.assertEqual(list(Path(self.uploads.name).iterdir()), [])
        with patch("geosite.services.MAX_REPORT_PDF_BYTES", len(raw)):
            self.assertEqual(self.submit(pdf=(io.BytesIO(raw), "../../my report.pdf")).status_code, 302)
        version = SubmittedReportVersion.query.one()
        self.assertEqual(version.original_filename, "my_report.pdf")
        self.assertRegex(version.filename, r"^[0-9a-f]{32}\.pdf$")
        self.app.config["MAX_CONTENT_LENGTH"] = 100
        self.assertEqual(self.submit().status_code, 413)
        self.assertEqual(SubmittedReportVersion.query.count(), 1)

    def test_actual_five_mb_pdf_is_accepted_for_submission_and_replacement(self):
        self.login(self.employee)
        limit = 5 * 1024 * 1024
        prefix, suffix = self.pdf_bytes().rsplit(b"startxref", 1)
        # A comment before startxref leaves the PDF's existing object offsets valid.
        padding = limit - len(prefix) - len(b"%\nstartxref") - len(suffix)
        raw = prefix + b"%" + b" " * padding + b"\nstartxref" + suffix
        self.assertEqual(len(raw), limit)

        self.assertEqual(self.submit(pdf=(io.BytesIO(raw), "full-size.pdf")).status_code, 302)
        report = SubmittedReport.query.one()
        self.assertEqual(Path(self.uploads.name, report.current_version.filename).read_bytes(), raw)
        self.assertEqual(
            self.edit(report, pdf=(io.BytesIO(raw), "replacement.pdf")).status_code, 302
        )
        db.session.refresh(report)
        self.assertEqual(report.revision_number, 2)
        self.assertEqual(Path(self.uploads.name, report.current_version.filename).read_bytes(), raw)

        for upload in (
            lambda: self.submit(pdf=(io.BytesIO(raw + b"\n"), "oversized.pdf")),
            lambda: self.edit(report, pdf=(io.BytesIO(raw + b"\n"), "oversized.pdf")),
        ):
            response = upload()
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"exceeds the 5 MB", response.data)
        self.assertEqual(SubmittedReport.query.count(), 1)
        self.assertEqual(SubmittedReportVersion.query.count(), 2)
        self.assertEqual(len(list(Path(self.uploads.name).iterdir())), 2)

    def test_invalid_replacement_does_not_modify_saved_submission(self):
        self.login(self.employee)
        self.submit()
        report = SubmittedReport.query.one()
        filename = report.current_version.filename
        response = self.edit(report, total_mandays="2", pdf=(io.BytesIO(b"bad"), "bad.pdf"))
        self.assertEqual(response.status_code, 400)
        db.session.refresh(report)
        self.assertEqual(report.current_version.total_mandays, 8)
        self.assertEqual(report.current_version.filename, filename)
        self.assertEqual(report.revision_number, 1)
        self.assertEqual(len(list(Path(self.uploads.name).iterdir())), 1)

    def test_stale_edit_is_rejected_without_saving_uploaded_file(self):
        self.login(self.employee)
        self.submit()
        report = SubmittedReport.query.one()
        self.edit(report, total_mandays="7")
        response = self.edit(
            report, revision_number="1", total_mandays="3",
            pdf=(io.BytesIO(self.pdf_bytes(200)), "stale.pdf"),
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"Reload the page", response.data)
        self.assertEqual(SubmittedReportVersion.query.count(), 2)
        self.assertEqual(report.current_version.total_mandays, 7)
        self.assertEqual(len(list(Path(self.uploads.name).iterdir())), 1)

    def test_failed_database_commit_and_concurrent_edit_remove_only_new_file(self):
        self.login(self.employee)
        with patch("geosite.routes.db.session.commit", side_effect=SQLAlchemyError("write failed")):
            with self.assertRaises(SQLAlchemyError):
                self.submit()
        self.assertEqual(SubmittedReport.query.count(), 0)
        self.assertEqual(list(Path(self.uploads.name).iterdir()), [])
        self.submit()
        report = SubmittedReport.query.one()
        original = report.current_version.filename
        with patch("geosite.routes.db.session.commit", side_effect=StaleDataError("changed")):
            response = self.edit(
                report, pdf=(io.BytesIO(self.pdf_bytes(200)), "conflicting.pdf"),
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SubmittedReportVersion.query.count(), 1)
        self.assertEqual([path.name for path in Path(self.uploads.name).iterdir()], [original])
        self.assertEqual(Path(self.uploads.name, original).read_bytes(), self.pdf_bytes())

    def test_optimistic_lock_prevents_concurrent_overwrites(self):
        self.login(self.employee)
        self.submit()
        report_id = SubmittedReport.query.one().id
        with Session(db.engine) as first, Session(db.engine) as second:
            first_report = first.get(SubmittedReport, report_id)
            second_report = second.get(SubmittedReport, report_id)
            first_report.revision_number += 1
            first.commit()
            second_report.revision_number += 1
            with self.assertRaises(StaleDataError):
                second.commit()

    def test_storage_errors_and_missing_download_are_explicit(self):
        self.login(self.employee)
        with patch("geosite.routes.save_report_pdf", side_effect=OSError("disk full")):
            with patch.object(self.app.logger, "exception") as log:
                response = self.submit()
                log.assert_called_once()
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"The PDF could not be saved", response.data)
        self.assertEqual(SubmittedReport.query.count(), 0)
        self.submit()
        report = SubmittedReport.query.one()
        version = report.current_version
        Path(self.uploads.name, version.filename).unlink()
        with patch.object(self.app.logger, "error") as log:
            self.assertEqual(self.client.get(self.download_url(report, version)).status_code, 404)
            log.assert_called_once()

    def test_storage_filename_collision_does_not_remove_existing_pdf(self):
        self.login(self.employee)
        self.submit()
        report = SubmittedReport.query.one()
        filename = report.current_version.filename
        with patch("geosite.services.uuid.uuid4", return_value=uuid.UUID(Path(filename).stem)):
            with patch.object(self.app.logger, "exception") as log:
                response = self.edit(report, pdf=(io.BytesIO(self.pdf_bytes(200)), "new.pdf"))
                log.assert_called_once()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(SubmittedReportVersion.query.count(), 1)
        self.assertEqual(Path(self.uploads.name, filename).read_bytes(), self.pdf_bytes())

    def test_report_history_survives_employee_deactivation(self):
        self.login(self.employee)
        self.submit()
        report = SubmittedReport.query.one()
        version = report.current_version
        self.login(self.admin)
        self.client.post(f"/admin/employees/{self.employee.id}/delete")
        db.session.refresh(self.employee)
        self.assertFalse(self.employee.is_active)
        self.assertEqual(SubmittedReportVersion.query.count(), 1)
        self.assertIn(b"(inactive)", self.client.get("/submitted-reports").data)
        download = self.client.get(self.download_url(report, version))
        self.assertEqual(download.status_code, 200)
        download.close()

    def test_list_pagination_keeps_employee_scope(self):
        self.login(self.employee)
        for _ in range(21):
            self.submit()
        self.assertIn(b"Page 1 of 2", self.client.get("/submitted-reports").data)
        self.assertIn(b"Page 2 of 2", self.client.get("/submitted-reports?page=2").data)
        self.login(self.other)
        self.assertIn(b"No submitted reports yet", self.client.get("/submitted-reports?page=2").data)


class SubmittedReportMigrationTests(unittest.TestCase):
    def test_upgrade_downgrade_and_reapply_preserve_existing_users(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "SECRET_KEY": "submitted-report-migration",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(directory) / "reports.db"),
                "UPLOAD_FOLDER": str(Path(directory) / "uploads"),
            })
            migrations = str(Path(__file__).resolve().parents[1] / "migrations")
            with app.app_context():
                try:
                    upgrade(directory=migrations, revision="d37e8f9a0b16")
                    db.session.execute(text("""
                        INSERT INTO "user"
                        (id, name, email, mobile, designation, password_hash, role, created_date, is_active)
                        VALUES (1, 'Existing', 'existing@example.com', '1',
                                'Engineer', 'test', 'employee', '2026-10-01', 1)
                    """))
                    db.session.commit()
                    user_id = 1
                    upgrade(directory=migrations)
                    report = SubmittedReport(employee_id=user_id, revision_number=1)
                    db.session.add(SubmittedReportVersion(
                        report=report, revision_number=1, start_date=date(2026, 10, 1),
                        end_date=date(2026, 10, 2), total_mandays=2,
                        filename="a.pdf", original_filename="report.pdf", pdf_replaced=True,
                    ))
                    db.session.commit()
                    self.assertEqual(SubmittedReport.query.one().current_version.total_mandays, 2)
                    db.session.remove()
                    downgrade(directory=migrations, revision="d37e8f9a0b16")
                    self.assertNotIn("submitted_report", inspect(db.engine).get_table_names())
                    self.assertNotIn("submitted_report_version", inspect(db.engine).get_table_names())
                    self.assertEqual(db.session.execute(text(
                        'SELECT name FROM "user" WHERE id = 1'
                    )).scalar_one(), "Existing")
                    db.session.commit()
                    upgrade(directory=migrations)
                    self.assertEqual(SubmittedReport.query.count(), 0)
                    self.assertEqual(db.session.get(User, user_id).name, "Existing")
                finally:
                    db.session.remove()
                    db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
