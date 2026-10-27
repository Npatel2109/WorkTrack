import re
from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from geosite import create_app
from geosite.extensions import db
from geosite.models import AppSettings, AuditLog, User


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.apk = Path(self.folder.name) / "test.apk"
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "android-test-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "UPLOAD_FOLDER": self.folder.name, "ANDROID_APK_PATH": str(self.apk),
            "ANDROID_BUNDLED_APK_PATH": str(Path(self.folder.name) / "bundled.apk"),
            "WTF_CSRF_ENABLED": False,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.admin = User(name="Admin", email="admin@example.com", mobile="1", role="admin")
        self.admin.set_password("test-password-123")
        self.employee = User(name="Engineer", email="employee@example.com", mobile="2")
        self.employee.set_password("test-password-123")
        db.session.add_all([self.admin, self.employee])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.folder.cleanup()

    def login(self, user):
        self.client.post("/login", data={"email": user.email, "password": "test-password-123"})


class AndroidDownloadTests(SettingsTestCase):
    def test_bundled_apk_download_and_private_override_keep_admin_switch(self):
        bundled = Path(self.app.config["ANDROID_BUNDLED_APK_PATH"])
        bundled.write_bytes(b"bundled apk fixture")
        self.assertEqual(self.client.get("/download/android").status_code, 404)
        self.login(self.admin)
        self.assertIn(b"APK: Ready", self.client.get("/admin/settings").data)
        self.client.post("/admin/settings", data={
            "section": "android", "android_download_enabled": "yes",
        })
        self.client.post("/logout")
        self.assertIn(b"Download Android app", self.client.get("/login").data)
        response = self.client.get("/download/android")
        self.assertEqual(response.data, b"bundled apk fixture")
        response.close()
        self.apk.write_bytes(b"private override fixture")
        response = self.client.get("/download/android")
        self.assertEqual(response.data, b"private override fixture")
        response.close()
        self.login(self.admin)
        self.client.post("/admin/settings", data={"section": "android"})
        self.assertEqual(self.client.get("/download/android").status_code, 404)
        self.assertNotIn(b"Download Android app", self.client.get("/login").data)

    def test_download_requires_both_setting_and_published_file(self):
        self.assertNotIn(b"Download Android app", self.client.get("/login").data)
        self.assertEqual(self.client.get("/download/android").status_code, 404)
        self.apk.write_bytes(b"test apk fixture")
        self.assertEqual(self.client.get("/download/android").status_code, 404)
        self.login(self.admin)
        response = self.client.post("/admin/settings", data={
            "section": "android", "android_download_enabled": "yes",
        }, follow_redirects=True)
        self.assertIn(b"settings saved", response.data)
        self.assertTrue(db.session.get(AppSettings, 1).android_download_enabled)
        self.assertIsNotNone(AuditLog.query.filter_by(action="android_settings_updated").first())
        self.client.post("/logout")
        login_page = self.client.get("/login").data
        self.assertIn(b"Download Android app", login_page)
        self.assertIn(b"https://worktrack.blackpurple.in", login_page)
        self.assertNotIn(b"enter this server address", login_page)
        response = self.client.get("/download/android")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/vnd.android.package-archive")
        self.assertEqual(response.data, b"test apk fixture")
        self.assertIn("WorkTrack.apk", response.headers["Content-Disposition"])
        response.close()
        self.apk.unlink()
        self.assertNotIn(b"Download Android app", self.client.get("/login").data)
        self.assertEqual(self.client.get("/download/android").status_code, 404)
        self.apk.write_bytes(b"test apk fixture")
        self.login(self.admin)
        self.client.post("/admin/settings", data={"section": "android"})
        self.assertFalse(db.session.get(AppSettings, 1).android_download_enabled)
        self.assertEqual(self.client.get("/download/android").status_code, 404)

    def test_admin_only_settings_and_csrf(self):
        self.assertEqual(self.client.get("/admin/settings").status_code, 302)
        self.login(self.employee)
        self.assertEqual(self.client.get("/admin/settings").status_code, 403)
        self.assertEqual(self.client.post("/admin/settings", data={"section": "mail"}).status_code, 403)
        self.client.post("/logout")
        self.login(self.admin)
        page = self.client.get("/admin/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"https://worktrack.blackpurple.in", page.data)
        self.assertEqual(self.client.post("/admin/settings", data={"section": "bad"}).status_code, 400)
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.client.post("/admin/settings", data={}).status_code, 400)


    def test_removed_tunnel_endpoints_are_unavailable(self):
        url = "/admin/settings/tunnel"
        self.assertEqual(self.client.post(url).status_code, 404)
        self.login(self.employee)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(self.client.get(url + "/status").status_code, 404)
        self.client.post("/logout")
        self.login(self.admin)
        for action in ("start", "stop"):
            self.assertEqual(self.client.post(url, data={"action": action}).status_code, 404)
        self.assertEqual(self.client.get(url + "/status").status_code, 404)
        self.assertNotIn("quick_tunnel", self.app.extensions)

    def test_settings_no_longer_offer_temporary_global_access(self):
        self.login(self.admin)
        response = self.client.get("/admin/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Temporary global access", response.data)
        self.assertNotIn(b"refresh-tunnel", response.data)
        self.assertIn(b"Email (password reset)", response.data)


class MailSettingsTests(SettingsTestCase):
    MAIL = {
        "section": "mail", "action": "save",
        "public_base_url": "http://worktrack.blackpurple.in/",
        "mail_server": "smtpout.secureserver.net", "mail_port": "465",
        "mail_security": "ssl", "mail_username": "noreply@blackpurple.in",
        "mail_password": "mailbox-secret", "mail_sender": "noreply@blackpurple.in",
    }

    def test_admin_saves_mail_settings_and_password_is_never_shown(self):
        self.login(self.admin)
        response = self.client.post("/admin/settings", data=self.MAIL, follow_redirects=True)
        self.assertIn(b"Email settings saved", response.data)
        self.assertNotIn(b"mailbox-secret", response.data)
        saved = db.session.get(AppSettings, 1)
        self.assertEqual((saved.mail_server, saved.mail_port, saved.mail_security),
                         ("smtpout.secureserver.net", 465, "ssl"))
        self.assertEqual(saved.mail_password, "mailbox-secret")
        self.assertEqual(saved.public_base_url, "http://worktrack.blackpurple.in")
        self.assertIsNotNone(AuditLog.query.filter_by(action="mail_settings_updated").first())
        self.assertNotIn("mailbox-secret", AuditLog.query.filter_by(action="mail_settings_updated").first().details)
        self.client.post("/admin/settings", data={**self.MAIL, "mail_password": ""})
        self.assertEqual(db.session.get(AppSettings, 1).mail_password, "mailbox-secret")
        self.client.post("/admin/settings", data={**self.MAIL, "mail_password": "", "clear_mail_password": "yes"})
        self.assertEqual(db.session.get(AppSettings, 1).mail_password, "")

    def test_invalid_mail_settings_are_rejected(self):
        self.login(self.admin)
        for field, value in (
            ("mail_port", "abc"), ("mail_port", "70000"), ("mail_security", "tls1"),
            ("mail_server", "smtp://host"), ("mail_server", "bad host"),
            ("mail_sender", "not-an-email"), ("mail_sender", ""),
            ("public_base_url", "ftp://example.com"), ("public_base_url", "http://example.com/path"),
            ("public_base_url", "http://example.com:99999"),
        ):
            with self.subTest(field=field, value=value):
                response = self.client.post("/admin/settings", data={**self.MAIL, field: value})
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"alert-danger", response.data)
                saved = db.session.get(AppSettings, 1)
                self.assertTrue(saved is None or saved.mail_server == "")

    def test_saved_settings_are_used_for_reset_and_test_email(self):
        self.login(self.admin)
        with patch("smtplib.SMTP_SSL") as smtp:
            response = self.client.post("/admin/settings", data={**self.MAIL, "action": "test"},
                                        follow_redirects=True)
        self.assertIn(b"Test email sent to admin@example.com", response.data)
        smtp.assert_called_once_with("smtpout.secureserver.net", 465, timeout=15)
        client = smtp.return_value.__enter__.return_value
        client.login.assert_called_once_with("noreply@blackpurple.in", "mailbox-secret")
        client.starttls.assert_not_called()
        self.client.post("/logout")
        with patch("smtplib.SMTP_SSL") as smtp:
            self.client.post("/forgot-password", data={"email": "employee@example.com"})
        message = smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
        self.assertEqual(message["To"], "employee@example.com")
        self.assertEqual(message["From"], "noreply@blackpurple.in")
        self.assertIn("http://worktrack.blackpurple.in/reset-password/", message.get_content())

    def test_failed_test_email_shows_error(self):
        self.login(self.admin)
        with patch("smtplib.SMTP_SSL", side_effect=OSError("connection timed out")):
            response = self.client.post("/admin/settings", data={**self.MAIL, "action": "test"},
                                        follow_redirects=True)
        self.assertIn(b"test email failed: connection timed out", response.data)
        self.assertEqual(db.session.get(AppSettings, 1).mail_server, "smtpout.secureserver.net")

    def test_starttls_security_uses_plain_smtp_then_starttls(self):
        self.login(self.admin)
        with patch("smtplib.SMTP") as smtp:
            self.client.post("/admin/settings", data={
                **self.MAIL, "action": "test", "mail_port": "587", "mail_security": "starttls",
            })
        smtp.assert_called_once_with("smtpout.secureserver.net", 587, timeout=15)
        smtp.return_value.__enter__.return_value.starttls.assert_called_once()


class BrandLogoTests(SettingsTestCase):
    def test_logo_upload_form_posts_its_csrf_token(self):
        self.login(self.admin)
        self.app.config["WTF_CSRF_ENABLED"] = True
        page = self.client.get("/admin/settings")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(match)

        logo = BytesIO()
        Image.new("RGB", (4, 4), "blue").save(logo, format="PNG")
        response = self.client.post(
            "/admin/settings",
            data={
                "csrf_token": match.group(1).decode(),
                "section": "brand",
                "action": "upload",
                "brand_logo": (BytesIO(logo.getvalue()), "company.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn(b"Brand logo uploaded", response.data)

    def test_admin_can_upload_logo_for_all_brand_marks_and_remove_it(self):
        self.assertIn(b'<span class="login-symbol">G</span>', self.client.get("/login").data)
        self.login(self.admin)
        logo = BytesIO()
        Image.new("RGB", (4, 4), "blue").save(logo, format="PNG")
        response = self.client.post(
            "/admin/settings",
            data={
                "section": "brand",
                "action": "upload",
                "brand_logo": (BytesIO(logo.getvalue()), "company.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn(b"Brand logo uploaded", response.data)
        saved = db.session.get(AppSettings, 1)
        self.assertTrue(saved.brand_logo_filename.endswith(".png"))
        logo_response = self.client.get("/brand-logo")
        self.assertEqual(logo_response.status_code, 200)
        self.assertEqual(logo_response.mimetype, "image/png")
        self.assertEqual(logo_response.headers["X-Content-Type-Options"], "nosniff")
        self.assertTrue(logo_response.data.startswith(b"\x89PNG"))
        self.assertIn(b'img class="brand-mark brand-logo-image"', response.data)

        self.client.post("/logout")
        for page in ("/login", "/forgot-password", "/reset-password/invalid"):
            with self.subTest(page=page):
                page_response = self.client.get(page)
                self.assertEqual(page_response.status_code, 200)
                self.assertIn(b'src="/brand-logo"', page_response.data)
                self.assertNotIn(b">G</span>", page_response.data)

        self.login(self.admin)
        self.client.post("/admin/settings", data={
            "section": "brand", "action": "remove",
        }, follow_redirects=True)
        self.assertIsNone(db.session.get(AppSettings, 1).brand_logo_filename)
        self.assertEqual(self.client.get("/brand-logo").status_code, 404)
        self.assertIn(b'<span class="brand-mark">G</span>', self.client.get("/admin/settings").data)

    def test_logo_upload_rejects_non_image_and_employee_cannot_change_logo(self):
        self.login(self.employee)
        self.assertEqual(
            self.client.post("/admin/settings", data={"section": "brand", "action": "remove"}).status_code,
            403,
        )
        self.client.post("/logout")
        self.login(self.admin)
        unsupported = BytesIO()
        Image.new("RGB", (1, 1), "red").save(unsupported, format="GIF")
        response = self.client.post(
            "/admin/settings",
            data={
                "section": "brand",
                "action": "upload",
                "brand_logo": (BytesIO(unsupported.getvalue()), "logo.gif"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn(b"Upload a PNG, JPEG, or WebP logo image.", response.data)
        self.assertIsNone(db.session.get(AppSettings, 1))
