import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geosite import create_app
from geosite.extensions import db
from geosite.models import AppSettings, AuditLog, User


class AndroidDownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.apk = Path(self.folder.name) / "test.apk"
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "android-test-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "UPLOAD_FOLDER": self.folder.name, "ANDROID_APK_PATH": str(self.apk),
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

    def test_download_requires_both_setting_and_published_file(self):
        self.assertNotIn(b"Download Android app", self.client.get("/login").data)
        self.assertEqual(self.client.get("/download/android").status_code, 404)
        self.apk.write_bytes(b"test apk fixture")
        self.assertEqual(self.client.get("/download/android").status_code, 404)
        self.login(self.admin)
        response = self.client.post("/admin/settings", data={
            "android_server_url": "https://worktrack.example.com/",
        }, follow_redirects=True)
        self.assertIn(b"settings saved", response.data)
        self.assertEqual(db.session.get(AppSettings, 1).android_server_url, "https://worktrack.example.com")
        self.assertIsNotNone(AuditLog.query.filter_by(action="android_settings_updated").first())
        self.client.post("/logout")
        self.assertIn(b"Download Android app", self.client.get("/login").data)
        self.assertIn(b"https://worktrack.example.com", self.client.get("/login").data)
        response = self.client.get("/download/android")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/vnd.android.package-archive")
        self.assertEqual(response.data, b"test apk fixture")
        self.assertIn("WorkTrack.apk", response.headers["Content-Disposition"])
        response.close()
        self.apk.unlink()
        self.assertNotIn(b"Download Android app", self.client.get("/login").data)
        self.assertEqual(self.client.get("/download/android").status_code, 404)
        self.login(self.admin)
        self.client.post("/admin/settings", data={"android_server_url": ""})
        self.assertEqual(db.session.get(AppSettings, 1).android_server_url, "")

    def test_admin_only_settings_and_csrf(self):
        self.assertEqual(self.client.get("/admin/settings").status_code, 302)
        self.login(self.employee)
        self.assertEqual(self.client.get("/admin/settings").status_code, 403)
        self.assertEqual(self.client.post("/admin/settings", data={}).status_code, 403)
        self.client.post("/logout")
        self.login(self.admin)
        self.assertEqual(self.client.get("/admin/settings").status_code, 200)
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.client.post("/admin/settings", data={}).status_code, 400)

    def test_invalid_servers_do_not_enable_download(self):
        self.login(self.admin)
        for url in (
            "http://example.com", "https://localhost",
            "https://127.0.0.1", "https://[::1]",
            "http://127.0.0.1:5000", "http://8.8.8.8", "http://172.32.0.1",
            "http://192.168.1.999", "http://169.254.1.1", "http://192.168.1.6:0",
            "https://example.com/path", "https://example.com?q=secret",
            "https://name:password@example.com", "https://example.com#section",
            "https://example.com:invalid", "https://example.com:99999",
            "https://exam ple.com", "https://example.com\\evil", "https://intranet",
        ):
            with self.subTest(url=url):
                response = self.client.post("/admin/settings", data={"android_server_url": url})
                self.assertIn(b"Enter an HTTPS domain or a private-network HTTP address", response.data)
                self.assertIsNone(db.session.get(AppSettings, 1))

    def test_private_network_addresses_and_https_domain_are_saved(self):
        self.apk.write_bytes(b"test apk fixture")
        self.login(self.admin)
        for url in (
            "http://192.168.1.6:5000", "http://10.0.0.5:5000",
            "http://172.16.0.5:5000", "http://172.31.255.254",
            "https://192.168.1.6:5000", "https://worktrack.example.com",
        ):
            with self.subTest(url=url):
                response = self.client.post("/admin/settings", data={"android_server_url": url})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(db.session.get(AppSettings, 1).android_server_url, url)
        self.client.post("/admin/settings", data={"android_server_url": "http://192.168.1.6:5000"})
        self.client.post("/logout")
        response = self.client.get("/login")
        self.assertIn(b"Download Android app", response.data)
        self.assertIn(b"Local HTTP test server", response.data)
        response = self.client.get("/download/android")
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_tunnel_controls_require_admin_confirmation_and_non_debug(self):
        url = "/admin/settings/tunnel"
        self.assertEqual(self.client.post(url).status_code, 302)
        self.login(self.employee)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.get(url + "/status").status_code, 403)
        self.client.post("/logout")
        self.login(self.admin)
        controller = self.app.extensions["quick_tunnel"]
        with patch.object(controller, "start") as start:
            response = self.client.post(url, data={"action": "start"}, follow_redirects=True)
            self.assertIn(b"Confirm that you want", response.data)
            start.assert_not_called()
            self.app.debug = True
            response = self.client.post(url, data={"action": "start", "confirm_public": "yes"}, follow_redirects=True)
            self.assertIn(b"restart without --debug", response.data)
            start.assert_not_called()
            self.app.debug = False
            self.client.post(url, data={"action": "start", "confirm_public": "yes"})
            start.assert_called_once()
        with patch.object(controller, "stop") as stop:
            self.client.post(url, data={"action": "stop"})
            stop.assert_called_once()
        self.assertEqual(self.client.post(url, data={"action": "bad"}).status_code, 400)
        self.assertEqual(self.client.get(url + "/status").json["running"], False)
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.client.post(url, data={"action": "start", "confirm_public": "yes"}).status_code, 400)

    def test_tunnel_start_errors_are_visible(self):
        self.login(self.admin)
        with patch.object(self.app.extensions["quick_tunnel"], "start",
                          side_effect=RuntimeError("cloudflared is not installed")):
            response = self.client.post("/admin/settings/tunnel", data={
                "action": "start", "confirm_public": "yes",
            }, follow_redirects=True)
        self.assertIn(b"cloudflared is not installed", response.data)
