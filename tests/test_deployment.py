import os
import tempfile
import unittest
from unittest import mock

from geosite import create_app
from geosite.extensions import db
from geosite.models import User


ADMIN_ENV = {
    "ADMIN_NAME": "Render Admin",
    "ADMIN_EMAIL": "Admin@Example.com",
    "ADMIN_MOBILE": "9999999999",
    "ADMIN_PASSWORD": "a-strong-password",
}


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.config = {
            "TESTING": True, "SECRET_KEY": "deploy-test-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite://", "UPLOAD_FOLDER": self.uploads.name,
        }
        self.app = create_app(self.config)
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.drop_all()
            db.engine.dispose()
        self.uploads.cleanup()

    def run_ensure_admin(self, env):
        with mock.patch.dict(os.environ, env):
            return self.app.test_cli_runner().invoke(args=["ensure-admin"])

    def test_ensure_admin_creates_once_from_environment(self):
        result = self.run_ensure_admin(ADMIN_ENV)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Administrator created: admin@example.com", result.output)
        result = self.run_ensure_admin({**ADMIN_ENV, "ADMIN_EMAIL": "other@example.com"})
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("already exists", result.output)
        with self.app.app_context():
            admins = User.query.filter_by(role="admin").all()
            self.assertEqual([admin.email for admin in admins], ["admin@example.com"])
            self.assertTrue(admins[0].check_password("a-strong-password"))

    def test_ensure_admin_skips_without_environment(self):
        env = {key: "" for key in ADMIN_ENV}
        result = self.run_ensure_admin(env)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("ADMIN_EMAIL", result.output)
        with self.app.app_context():
            self.assertEqual(User.query.count(), 0)

    def test_ensure_admin_rejects_short_password(self):
        result = self.run_ensure_admin({**ADMIN_ENV, "ADMIN_PASSWORD": "short"})
        self.assertNotEqual(result.exit_code, 0)

    def test_test_email_reports_missing_configuration(self):
        self.app.config.update(MAIL_SERVER="", MAIL_DEFAULT_SENDER="")
        result = self.app.test_cli_runner().invoke(args=["test-email", "a@example.com"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not configured", result.output)

    def test_test_email_sends_with_configured_smtp(self):
        self.app.config.update(
            MAIL_SERVER="smtp.example.com", MAIL_PORT=587, MAIL_USE_TLS=True,
            MAIL_USERNAME="user", MAIL_PASSWORD="pw", MAIL_DEFAULT_SENDER="from@example.com",
        )
        with mock.patch("smtplib.SMTP") as smtp:
            result = self.app.test_cli_runner().invoke(args=["test-email", "a@example.com"])
        self.assertEqual(result.exit_code, 0, result.output)
        client = smtp.return_value.__enter__.return_value
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("user", "pw")
        self.assertEqual(client.send_message.call_args[0][0]["To"], "a@example.com")

    def test_trust_proxy_uses_forwarded_scheme_and_client(self):
        with mock.patch.dict(os.environ, {"TRUST_PROXY": "1"}):
            app = create_app(self.config)

        @app.route("/proxy-check")
        def proxy_check():
            from flask import request

            return f"{request.scheme} {request.remote_addr}"

        response = app.test_client().get("/proxy-check", headers={
            "X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9",
        })
        self.assertEqual(response.get_data(as_text=True), "https 203.0.113.9")


if __name__ == "__main__":
    unittest.main()
