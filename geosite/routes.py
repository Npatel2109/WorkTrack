import io
import os
import smtplib
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func

from .extensions import db
from .models import AppSettings, AssignedSite, Attendance, AuditLog, Invoice, LeaveRequest, SiteReport, User, utcnow
from .services import (
    generate_invoice_pdf,
    generate_site_report_pdf,
    haversine_distance,
    merge_site_reports_pdf,
    public_base_url,
    save_data_image,
    send_email,
    send_password_reset_email,
    write_audit,
)

bp = Blueprint("main", __name__)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def employee_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _active_attendance():
    return (
        Attendance.query.filter_by(employee_id=current_user.id, status="onsite")
        .order_by(Attendance.checkin_time.desc())
        .first()
    )


def _valid_verification(value, flow, key, identifier):
    if (
        value.get("flow") != flow
        or value.get(key) != identifier
        or not value.get("verified_at")
    ):
        return False
    try:
        timestamp = datetime.fromisoformat(value["verified_at"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = utcnow() - timestamp
        return timedelta(0) <= age <= timedelta(minutes=10)
    except (TypeError, ValueError):
        return False


def _employee_site(site_id):
    site = AssignedSite.query.filter_by(
        id=site_id, employee_id=current_user.id, is_active=True
    ).first()
    if not site:
        abort(404)
    return site


def _attendance_for_user(attendance_id):
    attendance = db.session.get(Attendance, attendance_id)
    if not attendance:
        abort(404)
    if not current_user.is_admin and attendance.employee_id != current_user.id:
        abort(404)
    return attendance


def _read_coordinates():
    data = request.get_json(silent=True) or {}
    try:
        lat, lon = float(data.get("latitude")), float(data.get("longitude"))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
        return lat, lon, None
    except (TypeError, ValueError):
        return None, None, (jsonify(error="Valid GPS coordinates are required."), 400)


def _distance_to_site(site, lat, lon):
    try:
        distance = haversine_distance(site.latitude, site.longitude, lat, lon)
    except (TypeError, ValueError):
        return None
    return distance if distance <= site.allowed_radius else None


def _pdf_response(attendance):
    temporary = tempfile.NamedTemporaryFile(
        suffix=".pdf", dir=current_app.instance_path, delete=False
    )
    path = temporary.name
    temporary.close()
    try:
        generate_site_report_pdf(attendance, path, current_app.config["UPLOAD_FOLDER"])
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    response = send_file(
        path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"site-report-{attendance.id}.pdf",
    )
    response.call_on_close(lambda: Path(path).unlink(missing_ok=True))
    return response


def _date_filtered(query, start_date, end_date):
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(
        end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    return query.filter(Attendance.checkout_time >= start, Attendance.checkout_time < end)


def _reset_serializer():
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"], salt="geosite-password-reset"
    )


def _leaves_today(today):
    return LeaveRequest.query.join(LeaveRequest.employee).filter(
        LeaveRequest.status == "approved",
        LeaveRequest.start_date <= today,
        LeaveRequest.end_date >= today,
        User.is_active.is_(True),
    )


def _leave_counts(today):
    return {
        "pending_leave_count": LeaveRequest.query.filter_by(status="pending").count(),
        "on_leave_count": _leaves_today(today).with_entities(
            LeaveRequest.employee_id
        ).distinct().count(),
    }


def _live_rows(today):
    rows = []
    on_leave = {leave.employee_id for leave in _leaves_today(today)}
    employees = User.query.filter_by(role="employee").order_by(User.name).all()
    for employee in employees:
        active = (
            Attendance.query.filter_by(employee_id=employee.id, status="onsite")
            .order_by(Attendance.checkin_time.desc())
            .first()
        )
        assigned = next((site for site in employee.assigned_sites if site.is_active), None)
        site = active.site if active else assigned
        last_login_event = (
            AuditLog.query.filter(
                AuditLog.user_id == employee.id,
                AuditLog.action.in_(("login", "logout")),
            )
            .order_by(AuditLog.created_at.desc())
            .first()
        )
        last_completed = (
            Attendance.query.filter_by(employee_id=employee.id, status="completed")
            .order_by(Attendance.checkout_time.desc())
            .first()
        )
        completed_today = bool(
            last_completed
            and last_completed.checkout_time
            and last_completed.checkout_time.date() == today
        )
        status = (
            "onsite"
            if active
            else "on-leave"
            if employee.id in on_leave
            else "completed"
            if completed_today
            else "assigned"
            if assigned and last_login_event and last_login_event.action == "login"
            else "offline"
        )
        rows.append(
            {
                "employee": employee,
                "site": site,
                "attendance": active,
                "status": status,
            }
        )
    return rows


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(
            url_for(
                "main.admin_dashboard"
                if current_user.is_admin
                else "main.employee_dashboard"
            )
        )
    return redirect(url_for("main.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email, is_active=True).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            session["last_activity"] = utcnow().isoformat()
            write_audit(user, "login", ip_address=request.remote_addr)
            db.session.commit()
            return redirect(
                url_for(
                    "main.admin_dashboard"
                    if user.is_admin
                    else "main.employee_dashboard"
                )
            )
        flash("Email or password is incorrect.", "danger")
    if request.args.get("expired"):
        flash("Your session expired. Please sign in again.", "warning")
    settings = db.session.get(AppSettings, 1)
    return render_template(
        "login.html", android_server_url=current_app.config["ANDROID_SERVER_URL"],
        android_available=bool(settings and settings.android_download_enabled)
        and Path(current_app.config["ANDROID_APK_PATH"]).is_file(),
    )


def _clean_base_url(value):
    value = value.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        len(value) > 500 or parsed.scheme not in ("https", "http") or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path not in ("", "/") or any(character.isspace() for character in value)
        or "\\" in value
    ):
        raise ValueError("Enter the public address without a path, for example http://worktrack.blackpurple.in.")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Enter a valid port in the public address.") from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Enter a valid port in the public address.")
    return value


def _save_mail_settings(settings):
    form = request.form
    server = form.get("mail_server", "").strip().lower()
    sender = form.get("mail_sender", "").strip()
    username = form.get("mail_username", "").strip()
    security = form.get("mail_security", "ssl")
    if security not in ("ssl", "starttls", "none"):
        raise ValueError("Choose a valid connection security option.")
    try:
        port = int(form.get("mail_port", "465"))
    except ValueError:
        raise ValueError("Enter a numeric SMTP port.") from None
    if not 1 <= port <= 65535:
        raise ValueError("Enter an SMTP port between 1 and 65535.")
    if len(server) > 255 or any(character.isspace() or character in "/\\:@" for character in server):
        raise ValueError("Enter only the SMTP host name, for example smtpout.secureserver.net.")
    if server and not sender:
        raise ValueError("Enter the sender email address.")
    if sender:
        try:
            sender = validate_email(sender, check_deliverability=False).normalized
        except EmailNotValidError:
            raise ValueError("Enter a valid sender email address.") from None
    if len(username) > 255:
        raise ValueError("The SMTP username is too long.")
    password = form.get("mail_password", "")
    if len(password) > 500:
        raise ValueError("The SMTP password is too long.")
    settings.public_base_url = _clean_base_url(form.get("public_base_url", ""))
    settings.mail_server = server
    settings.mail_port = port
    settings.mail_security = security
    settings.mail_username = username
    settings.mail_sender = sender
    if form.get("clear_mail_password") == "yes":
        settings.mail_password = ""
    elif password:
        settings.mail_password = password


@bp.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def app_settings():
    settings = db.session.get(AppSettings, 1)
    if request.method == "POST":
        section = request.form.get("section", "android")
        if section not in ("android", "mail"):
            abort(400)
        if settings is None:
            settings = AppSettings(id=1)
            db.session.add(settings)
        if section == "android":
            settings.android_download_enabled = request.form.get("android_download_enabled") == "yes"
            write_audit(current_user, "android_settings_updated",
                        "Download enabled" if settings.android_download_enabled else "Download disabled")
            db.session.commit()
            flash("Android app settings saved.", "success")
            return redirect(url_for("main.app_settings"))
        try:
            _save_mail_settings(settings)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "danger")
            settings = db.session.get(AppSettings, 1)
        else:
            write_audit(current_user, "mail_settings_updated",
                        f"{settings.mail_server or 'environment settings'}:{settings.mail_port}")
            db.session.commit()
            if request.form.get("action") == "test":
                try:
                    send_email(
                        current_app, current_user.email, "WorkTrack test email",
                        "Your WorkTrack email settings are working. Password reset emails will "
                        f"link to {public_base_url(current_app)}.",
                    )
                except (OSError, RuntimeError, smtplib.SMTPException) as error:
                    current_app.logger.warning("Test email failed: %r", error)
                    flash(f"Settings saved, but the test email failed: {error}", "danger")
                else:
                    flash(f"Settings saved. Test email sent to {current_user.email}.", "success")
            else:
                flash("Email settings saved.", "success")
            return redirect(url_for("main.app_settings", _anchor="email-settings"))
    return render_template(
        "settings.html", settings=settings,
        android_server_url=current_app.config["ANDROID_SERVER_URL"],
        apk_ready=Path(current_app.config["ANDROID_APK_PATH"]).is_file(),
        tunnel=current_app.extensions["quick_tunnel"].snapshot(),
        env_mail_server=current_app.config["MAIL_SERVER"],
        env_public_url=current_app.config["PUBLIC_BASE_URL"],
    )


@bp.route("/admin/settings/tunnel", methods=["POST"])
@admin_required
def manage_tunnel():
    tunnel = current_app.extensions["quick_tunnel"]
    action = request.form.get("action")
    if action == "start":
        if request.form.get("confirm_public") != "yes":
            flash("Confirm that you want to make this app publicly reachable before creating a link.", "danger")
        elif current_app.debug:
            flash("Stop the debug server and restart without --debug before creating a public link.", "danger")
        else:
            try:
                tunnel.start()
            except RuntimeError as error:
                flash(str(error), "danger")
            else:
                write_audit(current_user, "public_tunnel_started", "Temporary public link requested")
                db.session.commit()
                flash("Creating the link. Refresh tunnel status in a few seconds.", "success")
    elif action == "stop":
        tunnel.stop()
        write_audit(current_user, "public_tunnel_stopped")
        db.session.commit()
        flash("Temporary public access stopped. Any saved Android address is unchanged; replace it if needed.", "success")
    else:
        abort(400)
    return redirect(url_for("main.app_settings"))


@bp.route("/admin/settings/tunnel/status")
@admin_required
def tunnel_status():
    return jsonify(current_app.extensions["quick_tunnel"].snapshot())


@bp.route("/download/android")
def download_android():
    settings = db.session.get(AppSettings, 1)
    path = Path(current_app.config["ANDROID_APK_PATH"])
    if not settings or not settings.android_download_enabled or not path.is_file():
        abort(404)
    response = send_file(
        path, mimetype="application/vnd.android.package-archive",
        as_attachment=True, download_name="WorkTrack.apk", max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email, is_active=True).first()
        if user:
            token = _reset_serializer().dumps(
                {"user_id": user.id, "password_hash": user.password_hash}
            )
            reset_url = (
                public_base_url(current_app)
                + url_for("main.reset_password", token=token)
            )
            try:
                send_password_reset_email(current_app, user.email, reset_url)
            except (OSError, RuntimeError, smtplib.SMTPException):
                current_app.logger.exception("Unable to send password reset email.")
        flash(
            "If an active account uses that email, a password reset link will be sent. "
            "If it does not arrive, contact the administrator.",
            "info",
        )
        return redirect(url_for("main.login"))
    return render_template("forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        data = _reset_serializer().loads(token, max_age=3600)
        user = db.session.get(User, int(data["user_id"]))
        if not user or not user.is_active or user.password_hash != data["password_hash"]:
            raise BadSignature("Password reset token is no longer valid.")
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        flash("That reset link is invalid or expired. Request another one.", "danger")
        return redirect(url_for("main.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 12:
            flash("Passwords must contain at least 12 characters.", "danger")
        elif password != request.form.get("confirm_password", ""):
            flash("The passwords do not match.", "danger")
        else:
            user.set_password(password)
            user.auth_version += 1
            write_audit(user, "password_reset", ip_address=request.remote_addr)
            db.session.commit()
            flash("Password reset. Sign in with your new password.", "success")
            return redirect(url_for("main.login"))
    return render_template("reset_password.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    if not current_user.is_admin and _active_attendance():
        flash("Complete your active site visit before signing out.", "warning")
        return redirect(url_for("main.employee_dashboard"))
    write_audit(current_user, "logout", ip_address=request.remote_addr)
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("main.login"))


@bp.route("/admin")
@admin_required
def admin_dashboard():
    today = datetime.now(timezone.utc).date()
    rows = _live_rows(today)
    completed_today = Attendance.query.filter(
        Attendance.status == "completed",
        func.date(Attendance.checkout_time) == today.isoformat(),
    ).count()
    week_start = today - timedelta(days=6)
    data = (
        db.session.query(func.date(Attendance.checkout_time), func.count(Attendance.id))
        .filter(
            Attendance.status == "completed",
            Attendance.checkout_time
            >= datetime.combine(week_start, datetime.min.time(), tzinfo=timezone.utc),
        )
        .group_by(func.date(Attendance.checkout_time))
        .all()
    )
    counts = {str(day): count for day, count in data}
    labels = [(week_start + timedelta(days=offset)).isoformat() for offset in range(7)]
    return render_template(
        "admin_dashboard.html",
        employee_count=User.query.filter_by(role="employee").count(),
        site_count=AssignedSite.query.filter_by(is_active=True).count(),
        onsite_count=Attendance.query.filter_by(status="onsite").count(),
        completed_today=completed_today,
        report_count=SiteReport.query.count(),
        live_rows=rows,
        chart_labels=labels,
        chart_values=[counts.get(day, 0) for day in labels],
        **_leave_counts(today),
    )


@bp.route("/admin/metrics/<metric>")
@admin_required
def admin_metric(metric):
    today = datetime.now(timezone.utc).date()
    rows = []
    headings = ("Employee", "Details", "Status", "Report")
    if metric == "employees":
        title = "Total employees"
        for user in User.query.filter_by(role="employee").order_by(User.name):
            rows.append(
                {
                    "employee": user.name,
                    "details": f"{user.email} - {user.designation}",
                    "status": "Active" if user.is_active else "Disabled",
                    "pdf_url": None,
                }
            )
    elif metric == "sites":
        title = "Assigned sites"
        for site in AssignedSite.query.filter_by(is_active=True).order_by(AssignedSite.site_name):
            rows.append(
                {
                    "employee": site.employee.name,
                    "details": f"{site.site_name} - {site.customer_name} - {site.address}",
                    "status": "Assigned",
                    "pdf_url": None,
                }
            )
    elif metric == "onsite":
        title = "Currently onsite"
        for visit in Attendance.query.filter_by(status="onsite").order_by(Attendance.checkin_time.desc()):
            rows.append(
                {
                    "employee": visit.employee.name,
                    "details": f"{visit.site.site_name} - {visit.checkin_time:%Y-%m-%d %H:%M UTC}",
                    "status": "Onsite",
                    "pdf_url": None,
                }
            )
    elif metric == "completed":
        title = "Completed today"
        visits = Attendance.query.filter(
            Attendance.status == "completed",
            func.date(Attendance.checkout_time) == today.isoformat(),
        ).order_by(Attendance.checkout_time.desc())
        for visit in visits:
            rows.append(
                {
                    "employee": visit.employee.name,
                    "details": f"{visit.site.site_name} - {visit.checkout_time:%Y-%m-%d %H:%M UTC}",
                    "status": "Completed",
                    "pdf_url": url_for("main.download_pdf", attendance_id=visit.id)
                    if visit.report
                    else None,
                }
            )
    elif metric == "reports":
        title = "Reports submitted"
        for report in SiteReport.query.join(Attendance).order_by(SiteReport.report_date.desc()):
            rows.append(
                {
                    "employee": report.attendance.employee.name,
                    "details": f"{report.attendance.site.site_name} - Submitted {report.report_date:%Y-%m-%d}",
                    "status": "Submitted",
                    "pdf_url": url_for("main.download_pdf", attendance_id=report.attendance_id),
                }
            )
    else:
        abort(404)
    return render_template(
        "admin_metric.html", title=title, headings=headings, rows=rows
    )


@bp.route("/api/admin/live-status")
@admin_required
def live_status():
    today = utcnow().date()
    rows = _live_rows(today)
    return jsonify(
        **_leave_counts(today),
        onsite_count=sum(row["status"] == "onsite" for row in rows),
        employees=[
            {
                "name": row["employee"].name,
                "designation": row["employee"].designation,
                "site": row["site"].site_name if row["site"] else None,
                "customer": row["site"].customer_name if row["site"] else None,
                "checkin_time": row["attendance"].checkin_time.strftime("%H:%M")
                if row["attendance"]
                else None,
                "status": row["status"],
            }
            for row in rows
        ],
    )


@bp.route("/admin/employees", methods=["GET", "POST"])
@admin_required
def employees():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = request.form.get("mobile", "").strip()
        designation = request.form.get("designation", "").strip()
        password = request.form.get("password", "")
        try:
            email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError:
            email = ""
        if not all((name, email, mobile, designation)) or len(password) < 12:
            flash("Enter employee details and a password of at least 12 characters.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("An account already uses that email address.", "danger")
        else:
            employee = User(
                name=name,
                email=email,
                mobile=mobile,
                designation=designation,
                role="employee",
            )
            employee.set_password(password)
            db.session.add(employee)
            write_audit(current_user, "employee_created", email)
            db.session.commit()
            flash("Employee created.", "success")
            return redirect(url_for("main.employees"))
    records = User.query.filter_by(role="employee").order_by(User.name).all()
    return render_template("employees.html", employees=records)


@bp.route("/admin/employees/<int:user_id>/edit", methods=["POST"])
@admin_required
def edit_employee(user_id):
    employee = User.query.filter_by(id=user_id, role="employee").first_or_404()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    mobile = request.form.get("mobile", "").strip()
    designation = request.form.get("designation", "").strip()
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        email = ""
    duplicate = User.query.filter(User.email == email, User.id != employee.id).first()
    if not all((name, email, mobile, designation)) or duplicate:
        flash("Enter valid employee details and a unique email address.", "danger")
    else:
        employee.name, employee.email = name, email
        employee.mobile, employee.designation = mobile, designation
        write_audit(current_user, "employee_updated", email)
        db.session.commit()
        flash("Employee details updated.", "success")
    return redirect(url_for("main.employees"))


@bp.route("/admin/employees/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_employee_password(user_id):
    employee = User.query.filter_by(id=user_id, role="employee").first_or_404()
    password = request.form.get("password", "")
    if len(password) < 12:
        flash("New passwords must contain at least 12 characters.", "danger")
    else:
        employee.set_password(password)
        employee.auth_version += 1
        write_audit(current_user, "employee_password_reset", employee.email)
        db.session.commit()
        flash("Employee password reset.", "success")
    return redirect(url_for("main.employees"))


@bp.route("/admin/employees/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_employee(user_id):
    employee = User.query.filter_by(id=user_id, role="employee").first_or_404()
    has_history = (
        Attendance.query.filter_by(employee_id=user_id).first()
        or Invoice.query.filter_by(employee_id=user_id).first()
        or LeaveRequest.query.filter_by(employee_id=user_id).first()
        or AuditLog.query.filter_by(user_id=user_id).first()
    )
    if has_history:
        employee.is_active = False
        write_audit(current_user, "employee_deactivated", employee.email)
        flash("Employee has history and was deactivated instead of deleted.", "warning")
    else:
        write_audit(current_user, "employee_deleted", employee.email)
        db.session.delete(employee)
        flash("Employee deleted.", "success")
    db.session.commit()
    return redirect(url_for("main.employees"))


@bp.route("/admin/sites", methods=["GET", "POST"])
@admin_required
def sites():
    if request.method == "POST":
        try:
            employee_id = int(request.form.get("employee_id", ""))
            latitude = float(request.form.get("latitude", ""))
            longitude = float(request.form.get("longitude", ""))
            radius = float(request.form.get("allowed_radius", "100"))
            employee = User.query.filter_by(
                id=employee_id, role="employee", is_active=True
            ).first()
            details = [
                request.form.get(key, "").strip()
                for key in ("site_name", "customer_name", "address")
            ]
            project_name = request.form.get("project_name", "").strip()
            po_number = request.form.get("po_number", "").strip()
            po_raw = request.form.get("po_date", "").strip()
            po_date = date.fromisoformat(po_raw) if po_raw else None
            if len(project_name) > 250 or len(po_number) > 120:
                raise ValueError
            signoff = _report_signoff_fields()
            if not employee or not all(details):
                raise ValueError
            if not (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
                and 10 <= radius <= 5000
            ):
                raise ValueError
        except (TypeError, ValueError):
            flash("Enter valid site details, coordinates, radius, employee and PO date. Keep company/manager fields within their limits and remarks to five lines (2,000 characters).", "danger")
        else:
            existing = AssignedSite.query.filter_by(
                employee_id=employee_id, is_active=True
            ).first()
            if existing and Attendance.query.filter_by(
                site_id=existing.id, status="onsite"
            ).first():
                flash("The employee is onsite; wait until the visit is complete.", "danger")
                return redirect(url_for("main.sites"))
            has_history = existing and (
                Attendance.query.filter_by(site_id=existing.id).first()
                or Invoice.query.filter_by(site_id=existing.id).first()
            )
            if existing and not has_history:
                existing.site_name, existing.customer_name, existing.address = details
                existing.latitude, existing.longitude = latitude, longitude
                existing.allowed_radius = radius
                site = existing
            else:
                if existing:
                    existing.is_active = False
                site = AssignedSite(
                    employee_id=employee_id,
                    site_name=details[0],
                    customer_name=details[1],
                    address=details[2],
                    latitude=latitude,
                    longitude=longitude,
                    allowed_radius=radius,
                )
                db.session.add(site)
            site.project_name, site.po_number, site.po_date = project_name, po_number, po_date
            for key, value in signoff.items():
                setattr(site, key, value)
            write_audit(current_user, "site_assigned", f"{site.site_name} -> {employee.email}")
            db.session.commit()
            flash("Site assignment saved.", "success")
            return redirect(url_for("main.sites"))
    active_sites = AssignedSite.query.filter_by(is_active=True).order_by(AssignedSite.site_name).all()
    employee_options = User.query.filter_by(role="employee", is_active=True).order_by(User.name).all()
    return render_template("sites.html", sites=active_sites, employees=employee_options)


def _report_signoff_fields():
    fields = {
        key: request.form.get(key, "").strip()
        for key in ("report_company", "commissioning_manager", "project_manager", "report_remarks")
    }
    if (
        len(fields["report_company"]) > 160
        or len(fields["commissioning_manager"]) > 120
        or len(fields["project_manager"]) > 120
        or len(fields["report_remarks"]) > 2000
        or len(fields["report_remarks"].splitlines()) > 5
    ):
        raise ValueError("Enter company/manager details within the field limits and at most five remarks lines (2,000 characters).")
    return fields


@bp.route("/admin/sites/<int:site_id>/report-details", methods=["POST"])
@admin_required
def site_report_details(site_id):
    site = db.get_or_404(AssignedSite, site_id)
    project = request.form.get("project_name", "").strip()
    po = request.form.get("po_number", "").strip()
    try:
        raw = request.form.get("po_date", "").strip()
        po_date = date.fromisoformat(raw) if raw else None
        if len(project) > 250 or len(po) > 120:
            raise ValueError
        signoff = _report_signoff_fields()
    except ValueError:
        flash("Enter a valid PO date, project (max 250 characters), PO number (max 120), company (max 160), manager names (max 120) and at most five remarks lines (max 2,000 characters).", "danger")
    else:
        site.project_name, site.po_number, site.po_date = project, po, po_date
        for key, value in signoff.items():
            setattr(site, key, value)
        write_audit(current_user, "site_report_header_updated", f"Site {site.id}")
        db.session.commit()
        flash("Report header saved.", "success")
    return redirect(url_for("main.sites"))


@bp.route("/admin/sites/<int:site_id>/delete", methods=["POST"])
@admin_required
def delete_site(site_id):
    site = db.session.get(AssignedSite, site_id)
    if not site:
        abort(404)
    if Attendance.query.filter_by(site_id=site_id, status="onsite").first():
        flash("This site cannot be removed during an active visit.", "danger")
    elif (
        Attendance.query.filter_by(site_id=site_id).first()
        or Invoice.query.filter_by(site_id=site_id).first()
    ):
        flash("This site has attendance or invoice history and cannot be deleted.", "danger")
    else:
        write_audit(current_user, "site_deleted", site.site_name)
        db.session.delete(site)
        db.session.commit()
        flash("Site assignment deleted.", "success")
    return redirect(url_for("main.sites"))


@bp.route("/employee")
@employee_required
def employee_dashboard():
    reports = (
        Attendance.query.filter_by(employee_id=current_user.id, status="completed")
        .order_by(Attendance.checkout_time.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "employee_dashboard.html",
        sites=AssignedSite.query.filter_by(employee_id=current_user.id, is_active=True).all(),
        active_attendance=_active_attendance(),
        reports=reports,
        leave_requests=LeaveRequest.query.filter_by(employee_id=current_user.id)
        .order_by(LeaveRequest.created_at.desc()).limit(5).all(),
    )


def _render_leaves(today, selected_status="all"):
    start_raw = request.args.get("from_date", "").strip()
    end_raw = request.args.get("to_date", "").strip()
    try:
        start = date.fromisoformat(start_raw) if start_raw else None
        end = date.fromisoformat(end_raw) if end_raw else None
    except ValueError:
        abort(400, description="Enter valid leave filter dates in YYYY-MM-DD format.")
    if start and end and end < start:
        abort(400, description="The filter end date must be on or after the start date.")

    employees = []
    employee_search = ""
    employee_id = ""
    query = LeaveRequest.query
    if current_user.is_admin:
        employee_search = request.args.get("employee_search", "").strip()
        employee_id = request.args.get("employee_id", "").strip()
        employee_query = User.query.filter_by(role="employee")
        if employee_search:
            term = employee_search.lower()
            employee_query = employee_query.filter(
                func.lower(User.name).contains(term, autoescape=True)
                | func.lower(User.email).contains(term, autoescape=True)
            )
        employees = employee_query.order_by(User.name, User.id).all()
        if employee_id:
            try:
                identifier = int(employee_id)
            except ValueError:
                abort(400, description="Select a valid employee.")
            selected = User.query.filter_by(id=identifier, role="employee").first()
            if not selected:
                abort(400, description="Select a valid employee.")
            employees = [employee for employee in employees if employee.id == identifier]
        query = query.filter(LeaveRequest.employee_id.in_([employee.id for employee in employees]))
    else:
        query = query.filter_by(employee_id=current_user.id)
    if start:
        query = query.filter(LeaveRequest.end_date >= start)
    if end:
        query = query.filter(LeaveRequest.start_date <= end)

    totals = {}
    for leave in query.filter_by(status="approved").all():
        overlap_start = max(leave.start_date, start) if start else leave.start_date
        overlap_end = min(leave.end_date, end or today, today)
        if overlap_start <= overlap_end:
            days = (overlap_end - overlap_start).days + 1 if leave.period == "full" else 0.5
            totals[leave.employee_id] = totals.get(leave.employee_id, 0) + days

    if selected_status == "today":
        query = query.filter(
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= today, LeaveRequest.end_date >= today,
            LeaveRequest.employee.has(User.is_active.is_(True)),
        )
    elif selected_status in ("pending", "approved", "rejected"):
        query = query.filter_by(status=selected_status)
    elif selected_status != "all":
        abort(400)
    return render_template(
        "leaves.html", today=today, selected_status=selected_status,
        leaves=query.order_by(LeaveRequest.created_at.desc()).all(),
        from_date=start_raw, to_date=end_raw, employee_search=employee_search,
        employee_id=employee_id, employee_options=User.query.filter_by(role="employee")
        .order_by(User.name, User.id).all() if current_user.is_admin else [],
        leave_total=sum(totals.values()),
        employee_totals=[(employee, totals.get(employee.id, 0)) for employee in employees],
    )


@bp.route("/employee/leaves", methods=["GET", "POST"])
@employee_required
def employee_leaves():
    today = utcnow().date()
    if request.method == "POST":
        period = request.form.get("period", "")
        reason = request.form.get("reason", "").strip()
        try:
            start = date.fromisoformat(request.form.get("start_date", ""))
            end = date.fromisoformat(request.form.get("end_date", ""))
            if start < today or end < start:
                raise ValueError("Choose dates from today onwards, with the end on or after the start.")
            if period not in ("full", "morning", "afternoon"):
                raise ValueError("Choose full-day leave or a morning/afternoon half day.")
            if period != "full" and start != end:
                raise ValueError("Half-day leave must start and end on the same date.")
            if not reason or len(reason) > 2000:
                raise ValueError("Enter a leave reason of 1 to 2,000 characters.")
        except ValueError as error:
            flash(f"Invalid leave request. {error}", "danger")
        else:
            overlaps = LeaveRequest.query.filter(
                LeaveRequest.employee_id == current_user.id,
                LeaveRequest.status.in_(("pending", "approved")),
                LeaveRequest.start_date <= end,
                LeaveRequest.end_date >= start,
            )
            if period != "full":
                overlaps = overlaps.filter(LeaveRequest.period.in_(("full", period)))
            if overlaps.first():
                flash("These dates overlap an existing pending or approved leave request.", "danger")
            else:
                leave = LeaveRequest(
                    employee_id=current_user.id, start_date=start, end_date=end,
                    period=period, reason=reason,
                )
                db.session.add(leave)
                write_audit(current_user, "leave_requested", f"{start} to {end} ({period})")
                db.session.commit()
                flash("Leave request submitted for administrator approval.", "success")
                return redirect(url_for("main.employee_leaves"))
    return _render_leaves(today)


@bp.route("/admin/leaves")
@admin_required
def admin_leaves():
    return _render_leaves(utcnow().date(), request.args.get("status", "all"))


@bp.route("/admin/leaves/<int:leave_id>/review", methods=["POST"])
@admin_required
def review_leave(leave_id):
    leave = db.get_or_404(LeaveRequest, leave_id)
    decision = request.form.get("decision")
    if decision not in ("approved", "rejected"):
        abort(400)
    if decision == "approved" and (not leave.employee.is_active or leave.end_date < utcnow().date()):
        flash("Expired requests and requests from inactive employees cannot be approved.", "danger")
    else:
        updated = LeaveRequest.query.filter_by(id=leave_id, status="pending").update({
            "status": decision, "reviewed_by_id": current_user.id, "reviewed_at": utcnow(),
        })
        if updated:
            write_audit(current_user, f"leave_{decision}", f"Leave request {leave_id}")
            db.session.commit()
            flash(f"Leave request {decision}.", "success")
        else:
            flash("This leave request has already been reviewed.", "warning")
    return redirect(url_for("main.admin_leaves"))


@bp.route("/visit/start/<int:site_id>", methods=["POST"])
@employee_required
def start_visit(site_id):
    site = _employee_site(site_id)
    if _active_attendance():
        flash("Finish your current visit before starting another.", "warning")
        return redirect(url_for("main.employee_dashboard"))
    session.pop("visit_verification", None)
    return redirect(url_for("main.checkin_location", site_id=site.id))


@bp.route("/visit/<int:site_id>/checkin-location")
@employee_required
def checkin_location(site_id):
    site = _employee_site(site_id)
    if _active_attendance():
        return redirect(url_for("main.employee_dashboard"))
    return render_template("location.html", site=site, flow="checkin")


@bp.route("/api/visit/<int:site_id>/verify-checkin", methods=["POST"])
@employee_required
def verify_checkin(site_id):
    site = _employee_site(site_id)
    latitude, longitude, error = _read_coordinates()
    if error:
        return error
    distance = haversine_distance(site.latitude, site.longitude, latitude, longitude)
    if distance > site.allowed_radius:
        session.pop("visit_verification", None)
        return jsonify(
            verified=False, distance=round(distance, 1), allowed=site.allowed_radius
        )
    session["visit_verification"] = {
        "flow": "checkin",
        "site_id": site.id,
        "latitude": latitude,
        "longitude": longitude,
        "verified_at": utcnow().isoformat(),
    }
    return jsonify(
        verified=True,
        distance=round(distance, 1),
        next=url_for("main.checkin_camera", site_id=site.id),
    )


@bp.route("/visit/<int:site_id>/camera")
@employee_required
def checkin_camera(site_id):
    site = _employee_site(site_id)
    verification = session.get("visit_verification", {})
    if (
        not _valid_verification(verification, "checkin", "site_id", site.id)
        or _distance_to_site(
            site, verification.get("latitude"), verification.get("longitude")
        )
        is None
    ):
        abort(403)
    return render_template("camera.html", site=site)


@bp.route("/visit/<int:site_id>/checkin", methods=["POST"])
@employee_required
def complete_checkin(site_id):
    site = _employee_site(site_id)
    verification = session.get("visit_verification", {})
    if (
        not _valid_verification(verification, "checkin", "site_id", site.id)
        or _distance_to_site(
            site, verification.get("latitude"), verification.get("longitude")
        )
        is None
        or _active_attendance()
    ):
        abort(403)
    try:
        selfie = save_data_image(
            request.form.get("selfie", ""), current_app.config["UPLOAD_FOLDER"]
        )
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("main.checkin_camera", site_id=site.id))
    attendance = Attendance(
        employee_id=current_user.id,
        site_id=site.id,
        checkin_lat=verification["latitude"],
        checkin_lon=verification["longitude"],
        checkin_selfie=selfie,
        status="onsite",
    )
    db.session.add(attendance)
    db.session.flush()
    write_audit(current_user, "site_checkin", f"Attendance {attendance.id}")
    db.session.commit()
    session.pop("visit_verification", None)
    flash("You are checked in at the assigned site.", "success")
    return redirect(url_for("main.employee_dashboard"))


@bp.route("/visit/<int:attendance_id>/checkout-location")
@employee_required
def checkout_location(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    if attendance.status != "onsite":
        abort(404)
    return render_template(
        "location.html", site=attendance.site, flow="checkout", attendance=attendance
    )


@bp.route("/api/visit/<int:attendance_id>/verify-checkout", methods=["POST"])
@employee_required
def verify_checkout(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    if attendance.status != "onsite":
        abort(404)
    latitude, longitude, error = _read_coordinates()
    if error:
        return error
    distance = haversine_distance(
        attendance.site.latitude,
        attendance.site.longitude,
        latitude,
        longitude,
    )
    if distance > attendance.site.allowed_radius:
        session.pop("visit_verification", None)
        return jsonify(
            verified=False,
            distance=round(distance, 1),
            allowed=attendance.site.allowed_radius,
        )
    session["visit_verification"] = {
        "flow": "checkout",
        "attendance_id": attendance.id,
        "latitude": latitude,
        "longitude": longitude,
        "verified_at": utcnow().isoformat(),
    }
    return jsonify(
        verified=True,
        distance=round(distance, 1),
        next=url_for("main.site_report_form", attendance_id=attendance.id),
    )


@bp.route("/visit/<int:attendance_id>/report", methods=["GET", "POST"])
@employee_required
def site_report_form(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    verification = session.get("visit_verification", {})
    if (
        attendance.status != "onsite"
        or not _valid_verification(
            verification, "checkout", "attendance_id", attendance.id
        )
        or _distance_to_site(
            attendance.site,
            verification.get("latitude"),
            verification.get("longitude"),
        )
        is None
    ):
        abort(403)
    if request.method == "POST":
        names = ("activity_detail", "billing", "engineer_name")
        values = {name: request.form.get(name, "").strip() for name in names}
        if not all(values.values()) or values["billing"] not in ("billable", "non_billable"):
            flash("Enter activity detail, select Billable or Non-billable, and enter the engineer name.", "danger")
            return render_template("site_report.html", attendance=attendance, values=values)
        attendance.checkout_lat = verification["latitude"]
        attendance.checkout_lon = verification["longitude"]
        attendance.checkout_time = utcnow()
        attendance.status = "completed"
        db.session.add(
            SiteReport(
                attendance=attendance,
                work_performed=values["activity_detail"],
                is_billable=values["billing"] == "billable",
                activities_completed="", issues_found="", recommendations="",
                materials_used="", remarks="",
                engineer_name=values["engineer_name"],
                customer_representative_name="",
                employee_signature="",
                customer_signature="",
            )
        )
        write_audit(current_user, "site_checkout", f"Attendance {attendance.id}")
        db.session.commit()
        session.pop("visit_verification", None)
        flash("Site visit completed successfully.", "success")
        return redirect(url_for("main.employee_dashboard"))
    return render_template("site_report.html", attendance=attendance, values={})


@bp.route("/reports")
@login_required
def reports():
    query = Attendance.query.filter_by(status="completed")
    if not current_user.is_admin:
        query = query.filter_by(employee_id=current_user.id)
    records = query.order_by(Attendance.checkout_time.desc()).all()
    sites_query = AssignedSite.query
    if not current_user.is_admin:
        sites_query = sites_query.filter_by(employee_id=current_user.id)
    return render_template(
        "reports.html", reports=records,
        activity_sites=sites_query.order_by(AssignedSite.site_name).all(),
        activity_employees=User.query.filter_by(role="employee").order_by(User.name).all()
        if current_user.is_admin else [],
    )


@bp.route("/reports/activity.xlsx")
@login_required
def download_activity_excel():
    try:
        start = date.fromisoformat(request.args.get("start_date", ""))
        end = date.fromisoformat(request.args.get("end_date", ""))
        site_id = int(request.args.get("site_id", ""))
        if end < start or (end - start).days > 366:
            raise ValueError
    except ValueError:
        flash("Select a site and valid start/end dates covering at most 367 days.", "danger")
        return redirect(url_for("main.reports"))
    query = AssignedSite.query.filter_by(id=site_id)
    if current_user.is_admin:
        try:
            employee_id = int(request.args.get("employee_id", ""))
        except ValueError:
            flash("Select an employee for the Excel report.", "danger")
            return redirect(url_for("main.reports", _anchor="activity-export"))
        query = query.filter_by(employee_id=employee_id)
    else:
        query = query.filter_by(employee_id=current_user.id)
    site = query.first_or_404()
    visits = _date_filtered(
        Attendance.query.filter_by(site_id=site.id, status="completed")
        .filter(Attendance.report.has()), start, end
    ).order_by(Attendance.checkout_time, Attendance.id).all()
    leaves = LeaveRequest.query.filter(
        LeaveRequest.employee_id == site.employee_id,
        LeaveRequest.status == "approved", LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    ).order_by(LeaveRequest.id).all()
    from .activity_report import activity_workbook

    output = activity_workbook(site, visits, leaves, start, end)
    return send_file(
        output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name=f"site-activity-{site.id}-{start}-{end}.xlsx",
    )


@bp.route("/reports/combined.pdf")
@login_required
def download_combined_reports():
    try:
        start_date = datetime.strptime(request.args.get("start_date", ""), "%Y-%m-%d").date()
        end_date = datetime.strptime(request.args.get("end_date", ""), "%Y-%m-%d").date()
    except ValueError:
        flash("Select a valid start and end date.", "danger")
        return redirect(url_for("main.reports"))
    if end_date < start_date:
        flash("End date cannot be earlier than start date.", "danger")
        return redirect(url_for("main.reports"))
    if (end_date - start_date).days > 3660:
        flash("Choose a date range of ten years or less.", "danger")
        return redirect(url_for("main.reports"))
    query = Attendance.query.filter(
        Attendance.status == "completed", Attendance.report.has()
    )
    if not current_user.is_admin:
        query = query.filter(Attendance.employee_id == current_user.id)
    records = _date_filtered(query, start_date, end_date).order_by(
        Attendance.checkout_time.asc()
    ).all()
    if not records:
        flash("No completed reports were found for that date range.", "warning")
        return redirect(url_for("main.reports"))
    if len(records) > 500:
        flash("More than 500 reports match; narrow the date range.", "warning")
        return redirect(url_for("main.reports"))
    output = merge_site_reports_pdf(records, current_app.config["UPLOAD_FOLDER"])
    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"geosite-reports-{start_date}-to-{end_date}.pdf",
    )


@bp.route("/reports/<int:attendance_id>/pdf")
@login_required
def download_pdf(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    if attendance.status != "completed" or not attendance.report:
        abort(404)
    return _pdf_response(attendance)


@bp.route("/admin/invoices", methods=["GET", "POST"])
@admin_required
def invoices():
    if request.method == "POST":
        try:
            site_id = int(request.form.get("site_id", ""))
            site = AssignedSite.query.filter_by(id=site_id, is_active=True).first()
            if not site:
                raise ValueError("Select an active assigned site.")
            start_date = datetime.strptime(request.form.get("start_date", ""), "%Y-%m-%d").date()
            end_date = datetime.strptime(request.form.get("end_date", ""), "%Y-%m-%d").date()
            billable_days = int(request.form.get("billable_days", ""))
            daily_rate = Decimal(request.form.get("daily_rate", "")).quantize(Decimal("0.01"))
            customer_name = request.form.get("customer_name", "").strip()
            description = request.form.get("description", "").strip()
            currency = request.form.get("currency", "").strip().upper()
            if end_date < start_date:
                raise ValueError("End date must be on or after start date.")
            if not 1 <= billable_days <= (end_date - start_date).days + 1:
                raise ValueError("Billable days must fit the selected date range.")
            if (
                not daily_rate.is_finite()
                or daily_rate <= 0
                or daily_rate > Decimal("9999999999.99")
            ):
                raise ValueError("Enter a valid daily rate greater than zero.")
            if not customer_name or len(customer_name) > 160 or not description:
                raise ValueError("Customer name and invoice description are required.")
            if currency not in {"INR", "USD", "EUR", "GBP", "AED"}:
                raise ValueError("Select a supported invoice currency.")
        except (InvalidOperation, TypeError, ValueError) as error:
            flash(str(error) or "Complete all required invoice fields.", "danger")
        else:
            invoice = Invoice(
                invoice_number=f"GS-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}",
                employee_id=site.employee_id,
                site_id=site.id,
                customer_name=customer_name,
                description=description,
                start_date=start_date,
                end_date=end_date,
                billable_days=billable_days,
                daily_rate=daily_rate,
                total_amount=(daily_rate * billable_days).quantize(Decimal("0.01")),
                currency=currency,
                created_by_id=current_user.id,
            )
            db.session.add(invoice)
            db.session.flush()
            write_audit(
                current_user,
                "invoice_created",
                f"{invoice.invoice_number}: {currency} {invoice.total_amount}",
            )
            db.session.commit()
            flash(f"Invoice {invoice.invoice_number} generated.", "success")
            return redirect(url_for("main.invoices"))
    site_options = (
        AssignedSite.query.filter_by(is_active=True)
        .join(User)
        .order_by(User.name, AssignedSite.site_name)
        .all()
    )
    invoice_records = Invoice.query.order_by(Invoice.created_at.desc()).limit(500).all()
    return render_template("invoices.html", sites=site_options, invoices=invoice_records)


@bp.route("/admin/invoices/<int:invoice_id>/pdf")
@admin_required
def download_invoice(invoice_id):
    invoice = db.session.get(Invoice, invoice_id)
    if not invoice:
        abort(404)
    output = io.BytesIO()
    generate_invoice_pdf(invoice, output)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{invoice.invoice_number}.pdf",
    )


@bp.route("/admin/reports/export.xlsx")
@admin_required
def export_excel():
    if not request.args:
        return redirect(url_for("main.reports", _anchor="activity-export"))
    return download_activity_excel()


@bp.route("/admin/attendance")
@admin_required
def attendance_history():
    visits = Attendance.query.order_by(Attendance.checkin_time.desc()).limit(500).all()
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template("attendance.html", attendance=visits, audit_logs=logs)


@bp.route("/media/<path:filename>")
@login_required
def media(filename):
    safe_name = Path(filename).name
    if safe_name != filename or safe_name in {"", ".", ".."}:
        abort(404)
    attendance = Attendance.query.filter_by(checkin_selfie=safe_name).first()
    if not attendance:
        report = SiteReport.query.filter(
            (SiteReport.employee_signature == safe_name)
            | (SiteReport.customer_signature == safe_name)
        ).first()
        attendance = report.attendance if report else None
    if not attendance:
        abort(404)
    if not current_user.is_admin and attendance.employee_id != current_user.id:
        abort(404)
    return send_file(
        os.path.join(current_app.config["UPLOAD_FOLDER"], safe_name),
        mimetype="image/png",
        as_attachment=False,
        download_name=safe_name,
    )


@bp.app_errorhandler(403)
def forbidden(_error):
    return render_template("error.html", status=403), 403


@bp.app_errorhandler(404)
def not_found(_error):
    return render_template("error.html", status=404), 404
