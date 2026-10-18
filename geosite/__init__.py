import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from flask import Flask, redirect, request, session, url_for
from flask_login import current_user, logout_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect

from .extensions import db, login_manager

csrf = CSRFProtect()
migrate = Migrate()


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", "sqlite:///geosite.db"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=os.environ.get(
            "UPLOAD_FOLDER", str(Path(app.instance_path) / "uploads")
        ),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)),
        SESSION_TIMEOUT_MINUTES=int(os.environ.get("SESSION_TIMEOUT_MINUTES", 60)),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        MAIL_SERVER=os.environ.get("MAIL_SERVER", ""),
        MAIL_PORT=int(os.environ.get("MAIL_PORT", 587)),
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME", ""),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD", ""),
        MAIL_USE_TLS=os.environ.get("MAIL_USE_TLS", "1") == "1",
        MAIL_DEFAULT_SENDER=os.environ.get("MAIL_DEFAULT_SENDER", ""),
        PUBLIC_BASE_URL=os.environ.get(
            "PUBLIC_BASE_URL", "http://127.0.0.1:5000"
        ),
    )
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = app.config[
            "SQLALCHEMY_DATABASE_URI"
        ].replace("postgres://", "postgresql+psycopg://", 1)
    elif app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = app.config[
            "SQLALCHEMY_DATABASE_URI"
        ].replace("postgresql://", "postgresql+psycopg://", 1)
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401
    from .routes import bp

    app.register_blueprint(bp)
    login_manager.login_view = "main.login"
    login_manager.login_message_category = "warning"

    @app.before_request
    def enforce_idle_timeout():
        if not current_user.is_authenticated:
            return None
        now = datetime.now(timezone.utc)
        raw_last_activity = session.get("last_activity")
        if raw_last_activity:
            try:
                last_activity = datetime.fromisoformat(raw_last_activity)
            except (TypeError, ValueError):
                last_activity = now
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
            if now - last_activity > timedelta(
                minutes=app.config["SESSION_TIMEOUT_MINUTES"]
            ):
                from .models import AuditLog

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="logout",
                        details="Session timeout",
                        ip_address=request.remote_addr,
                    )
                )
                db.session.commit()
                logout_user()
                session.clear()
                return redirect(url_for("main.login", expired=1))
        session["last_activity"] = now.isoformat()
        return None

    @app.context_processor
    def inject_year():
        return {"current_year": datetime.now().year}

    @app.errorhandler(413)
    def request_too_large(_error):
        from flask import flash, render_template

        flash("The upload exceeds the maximum request size.", "danger")
        return render_template("error.html", status=413), 413

    @app.cli.command("init-admin")
    @click.option("--name", prompt=True)
    @click.option("--email", prompt=True)
    @click.option("--mobile", prompt=True)
    @click.password_option()
    def init_admin(name, email, mobile, password):
        """Create the first administrator account."""
        from email_validator import EmailNotValidError, validate_email

        from .models import User

        try:
            email = validate_email(
                email.strip().lower(), check_deliverability=False
            ).normalized
        except EmailNotValidError as error:
            raise click.ClickException("Enter a valid email address.") from error
        if len(password) < 12:
            raise click.ClickException("Passwords must contain at least 12 characters.")
        if User.query.filter_by(email=email).first():
            raise click.ClickException("An account already uses that email address.")
        admin = User(
            name=name.strip(),
            email=email,
            mobile=mobile.strip(),
            designation="Administrator",
            role="admin",
        )
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        click.echo(f"Administrator created: {admin.email}")

    return app
