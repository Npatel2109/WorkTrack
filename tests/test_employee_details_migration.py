import tempfile
import unittest
from pathlib import Path

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from geosite import create_app
from geosite.extensions import db


class EmployeeDetailsMigrationTests(unittest.TestCase):
    def test_existing_users_survive_upgrade_and_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "SECRET_KEY": "migration-test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(directory) / "employees.db"),
                "UPLOAD_FOLDER": str(Path(directory) / "uploads"),
            })
            migrations = str(Path(__file__).resolve().parents[1] / "migrations")
            with app.app_context():
                try:
                    upgrade(directory=migrations, revision="c2d3e4f5a6b7")
                    db.session.execute(text("""
                        INSERT INTO "user"
                        (id, name, email, mobile, designation, password_hash, role, created_date, is_active)
                        VALUES (1, 'Existing Engineer', 'existing@example.com', '123',
                                'Engineer', 'existing-hash', 'employee', '2026-10-01', 1)
                    """))
                    db.session.commit()
                    upgrade(directory=migrations)
                    details = db.session.execute(text("""
                        SELECT address, emergency_contact_no, blood_group,
                               laptop_serial_no, employee_code, birthday, password_hash
                        FROM "user" WHERE id = 1
                    """)).one()
                    self.assertEqual(tuple(details), ("", "", "", "", None, None, "existing-hash"))
                    constraints = inspect(db.engine).get_unique_constraints("user")
                    self.assertTrue(any(item["column_names"] == ["employee_code"] for item in constraints))
                    db.session.execute(text("""
                        UPDATE "user" SET address = 'Test address', employee_code = 'EMP-01',
                                          birthday = '1996-02-29'
                        WHERE id = 1
                    """))
                    db.session.commit()
                    downgrade(directory=migrations, revision="d3e4f5a6b7c8")
                    self.assertNotIn("birthday", {
                        column["name"] for column in inspect(db.engine).get_columns("user")
                    })
                    self.assertEqual(db.session.execute(text(
                        'SELECT employee_code, address FROM "user" WHERE id = 1'
                    )).one(), ("EMP-01", "Test address"))
                    db.session.commit()
                    upgrade(directory=migrations)
                    downgrade(directory=migrations, revision="c2d3e4f5a6b7")
                    self.assertNotIn("employee_code", {
                        column["name"] for column in inspect(db.engine).get_columns("user")
                    })
                    self.assertEqual(db.session.execute(text(
                        'SELECT name, password_hash FROM "user" WHERE id = 1'
                    )).one(), ("Existing Engineer", "existing-hash"))
                    db.session.commit()
                    upgrade(directory=migrations)
                finally:
                    db.session.remove()
                    db.engine.dispose()
