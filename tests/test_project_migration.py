import tempfile
import unittest
from pathlib import Path

from flask_migrate import upgrade
from sqlalchemy import text

from geosite import create_app
from geosite.extensions import db


class ProjectMigrationTests(unittest.TestCase):
    def test_upgrade_resumes_partial_sqlite_schema_and_migrates_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "project-migration-test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(directory) / "projects.db"),
                "UPLOAD_FOLDER": str(Path(directory) / "uploads"),
            })
            migrations = str(Path(__file__).resolve().parents[1] / "migrations")
            with app.app_context():
                try:
                    upgrade(directory=migrations, revision="f60a0b1c2d39")
                    assigned_at = "2026-10-20 05:00:00"
                    for site_id, project_name in (
                        (1, "Legacy project"),
                        (2, "legacy PROJECT"),
                        (3, ""),
                    ):
                        db.session.execute(text("""
                            INSERT INTO assigned_sites
                            (id, site_name, customer_name, address, latitude, longitude,
                             allowed_radius, assigned_date, is_active, project_name, report_company)
                            VALUES (:id, :site, 'Customer', 'Address', 19, 72, 100,
                                    :assigned, 1, :project_name, 'Old company')
                        """), {
                            "id": site_id,
                            "site": f"Site {site_id}",
                            "assigned": assigned_at,
                            "project_name": project_name,
                        })
                    db.session.commit()

                    db.session.execute(text("""
                        CREATE TABLE project (
                            id INTEGER PRIMARY KEY,
                            name VARCHAR(250) NOT NULL UNIQUE
                        )
                    """))
                    db.session.execute(text("""
                        ALTER TABLE assigned_sites
                        ADD COLUMN project_id INTEGER REFERENCES project(id)
                    """))
                    db.session.execute(text("""
                        CREATE INDEX ix_assigned_sites_project_id
                        ON assigned_sites(project_id)
                    """))
                    db.session.execute(text(
                        "INSERT INTO project (id, name) VALUES (10, 'Legacy project')"
                    ))
                    db.session.execute(text("""
                        CREATE TABLE _alembic_tmp_assigned_sites AS
                        SELECT * FROM assigned_sites
                    """))
                    db.session.commit()

                    upgrade(directory=migrations)

                    projects = db.session.execute(text(
                        "SELECT id, project_number, name FROM project ORDER BY id"
                    )).all()
                    self.assertEqual(projects, [(10, "LEGACY-10", "Legacy project")])
                    sites = db.session.execute(text("""
                        SELECT id, project_id, project_name, report_company
                        FROM assigned_sites ORDER BY id
                    """)).all()
                    self.assertEqual(sites, [
                        (1, 10, "Legacy project", "Blackpurple automation"),
                        (2, 10, "legacy PROJECT", "Blackpurple automation"),
                        (3, None, "", "Blackpurple automation"),
                    ])
                finally:
                    db.session.remove()
                    db.engine.dispose()
