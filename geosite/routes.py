import io
import os
import re
import smtplib
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from werkzeug.utils import secure_filename
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
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.exc import StaleDataError

from .extensions import db
from .attendance import daily_cutoff, stop_attendance
from .timeutils import INDIA_TIME, format_ist, india_date
from .models import AppSettings, AssignedSite, Attendance, AttendancePolicy, AuditLog, BLOOD_GROUPS, CandidateReferral, DailyWorkStatus, Invoice, LeaveAllowanceAccount, LeaveRequest, MonthlyLeaveEntitlement, Project, PurchaseOrder, REPORT_COMPANY_NAME, SiteAssignment, SiteReport, SubmittedReport, SubmittedReportVersion, User, utcnow
from .work_status import approved_leave_today, attendance_summary, leave_overlap, month_bounds, monthly_leave_balance
from .services import (
    generate_invoice_pdf,
    generate_site_report_pdf,
    haversine_distance,
    merge_site_reports_pdf,
    public_base_url,
    save_data_image,
    save_report_pdf,
    send_email,
    send_password_reset_email,
    save_brand_logo,
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


def _attendance_hours(visit, now=None):
    if not visit.checkin_time:
        return 0

    def normalized(value):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    start = normalized(visit.checkin_time)
    end = normalized(visit.checkout_time or now or utcnow())
    return max(0, (end - start).total_seconds() / 3600)


def _format_hours(hours):
    total_minutes = max(0, round(hours * 60))
    return f"{total_minutes // 60}h {total_minutes % 60:02d}m"


def _used_po_mandays(po):
    start = datetime.combine(po.start_date, datetime.min.time(), tzinfo=INDIA_TIME)
    end = datetime.combine(po.end_date + timedelta(days=1), datetime.min.time(), tzinfo=INDIA_TIME)
    visits = Attendance.query.filter(
        Attendance.employee_id == po.employee_id,
        Attendance.site_id == po.site_id,
        Attendance.checkin_time >= start.astimezone(timezone.utc),
        Attendance.checkin_time < end.astimezone(timezone.utc),
        Attendance.checkout_time.isnot(None),
        Attendance.status.in_(("completed", "report_pending")),
    ).all()
    return len({india_date(visit.checkin_time).isoformat() for visit in visits})


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
    site = AssignedSite.query.filter_by(id=site_id, is_active=True).filter(
        AssignedSite.assignments.any(employee_id=current_user.id, is_active=True)
    ).first()
    if not site:
        abort(404)
    return site


def _lock_employee(employee_id):
    return User.query.filter_by(id=employee_id).with_for_update().first_or_404()


def _full_day_leave_block():
    if approved_leave_today(current_user.id, india_date(utcnow()))[1]:
        return "You are on approved full-day leave today. Site starts are blocked."
    return None


def _attendance_for_user(attendance_id):
    attendance = db.session.get(Attendance, attendance_id)
    if not attendance:
        abort(404)
    if not current_user.is_admin and attendance.employee_id != current_user.id:
        abort(404)
    return attendance


def _read_coordinates():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return None, None, (jsonify(error="A JSON object with GPS coordinates is required."), 400)
    try:
        lat, lon = float(data.get("latitude")), float(data.get("longitude"))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
        return lat, lon, None
    except (TypeError, ValueError):
        return None, None, (jsonify(error="Valid GPS coordinates are required."), 400)


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


def _date_filtered(query, start_date, end_date, tz=timezone.utc):
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(
        end_date + timedelta(days=1), datetime.min.time(), tzinfo=tz
    ).astimezone(timezone.utc)
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
    employee_ids = [employee.id for employee in employees]
    work_statuses = {}
    leave_statuses = {}
    if employee_ids:
        for item in DailyWorkStatus.query.filter(
            DailyWorkStatus.employee_id.in_(employee_ids),
            DailyWorkStatus.work_date == today,
        ).options(joinedload(DailyWorkStatus.reviewed_by)).all():
            work_statuses.setdefault(item.employee_id, []).append(item)
        for item in LeaveRequest.query.filter(
            LeaveRequest.employee_id.in_(employee_ids),
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today,
        ).options(joinedload(LeaveRequest.reviewed_by)).order_by(
            LeaveRequest.created_at.desc(), LeaveRequest.id.desc()
        ).all():
            leave_statuses.setdefault(item.employee_id, []).append(item)
    for employee in employees:
        active = (
            Attendance.query.filter_by(employee_id=employee.id, status="onsite")
            .order_by(Attendance.checkin_time.desc())
            .first()
        )
        pending = (
            Attendance.query.filter_by(employee_id=employee.id, status="report_pending")
            .order_by(Attendance.checkout_time.desc()).first()
        )
        assignments = sorted(
            (assignment.site for assignment in employee.site_assignments
             if assignment.is_active and assignment.site.is_active),
            key=lambda site: (site.site_name, site.id),
        )
        assigned = assignments[0] if assignments else None
        site = active.site if active else pending.site if pending else assigned
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
            and india_date(last_completed.checkout_time) == today
        )
        daily_statuses = []
        if active:
            daily_statuses.append({
                "label": "On Site", "status": "onsite", "detail": active.site.site_name,
            })
        if pending and pending.checkout_time and india_date(pending.checkout_time) == today:
            daily_statuses.append({
                "label": "Visit stopped - report pending",
                "status": "report-pending",
                "detail": pending.site.site_name,
            })
        if completed_today:
            daily_statuses.append({
                "label": "Site visit completed",
                "status": "completed",
                "detail": last_completed.site.site_name,
            })
        for item in work_statuses.get(employee.id, []):
            detail = item.reason
            if item.reviewed_by and item.reviewed_at:
                detail += f" · Reviewed by {item.reviewed_by.name} at {format_ist(item.reviewed_at, '%H:%M IST')}"
            daily_statuses.append({
                "label": item.type_label, "status": item.status, "detail": detail,
            })
        for item in leave_statuses.get(employee.id, []):
            detail = item.reason
            if item.reviewed_by and item.reviewed_at:
                detail += f" · Reviewed by {item.reviewed_by.name} at {format_ist(item.reviewed_at, '%H:%M IST')}"
            daily_statuses.append({
                "label": f"Leave - {item.period_label}",
                "status": item.status,
                "detail": detail,
            })
        if not daily_statuses:
            daily_statuses.append({
                "label": "No status submitted", "status": "offline", "detail": "",
            })
        status = (
            "onsite"
            if active
            else "on-leave"
            if employee.id in on_leave
            else "report-pending"
            if pending
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
                "sites": [active.site] if active else [pending.site] if pending else assignments,
                "attendance": active or pending,
                "onsite_hours": _format_hours(_attendance_hours(active or pending)) if (active or pending) else "—",
                "status": status,
                "daily_statuses": daily_statuses,
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
        and _android_apk_path().is_file(),
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
    referral_email = form.get("referral_notification_email", "").strip()
    po_email = form.get("po_notification_email", "").strip()
    for label, value in (("Referral", referral_email), ("PO man-day", po_email)):
        if value:
            try:
                value = validate_email(value, check_deliverability=False).normalized
            except EmailNotValidError:
                raise ValueError(f"Enter a valid {label} notification email address.") from None
    settings.referral_notification_email = referral_email
    settings.po_notification_email = po_email
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
        if section not in ("android", "mail", "brand"):
            abort(400)
        if settings is None:
            settings = AppSettings(id=1)
            db.session.add(settings)
        if section == "brand":
            action = request.form.get("action")
            old_filename = settings.brand_logo_filename
            if action == "upload":
                try:
                    filename = save_brand_logo(
                        request.files.get("brand_logo"),
                        Path(current_app.config["UPLOAD_FOLDER"]) / "brand-logos",
                    )
                except ValueError as error:
                    db.session.rollback()
                    flash(str(error), "danger")
                    return redirect(url_for("main.app_settings", _anchor="brand-settings"))
                settings.brand_logo_filename = filename
                details = "Brand logo uploaded"
            elif action == "remove":
                settings.brand_logo_filename = None
                details = "Brand logo removed"
            else:
                abort(400)
            write_audit(current_user, "brand_logo_updated", details)
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                if action == "upload":
                    (Path(current_app.config["UPLOAD_FOLDER"]) / "brand-logos" / filename).unlink(missing_ok=True)
                raise
            if (
                old_filename
                and old_filename != settings.brand_logo_filename
                and re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg|webp)", old_filename)
            ):
                (Path(current_app.config["UPLOAD_FOLDER"]) / "brand-logos" / old_filename).unlink(missing_ok=True)
            flash(f"{details}.", "success")
            return redirect(url_for("main.app_settings", _anchor="brand-settings"))
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
        apk_ready=_android_apk_path().is_file(),
        env_mail_server=current_app.config["MAIL_SERVER"],
        env_public_url=current_app.config["PUBLIC_BASE_URL"],
    )


@bp.route("/download/android")
def download_android():
    settings = db.session.get(AppSettings, 1)
    path = _android_apk_path()
    if not settings or not settings.android_download_enabled or not path.is_file():
        abort(404)
    response = send_file(
        path, mimetype="application/vnd.android.package-archive",
        as_attachment=True, download_name="WorkTrack.apk", max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/brand-logo")
def brand_logo():
    settings = db.session.get(AppSettings, 1)
    filename = settings.brand_logo_filename if settings else None
    if not filename or not re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg|webp)", filename):
        abort(404)
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "brand-logos" / filename
    if not path.is_file():
        abort(404)
    mimetype = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[path.suffix[1:]]
    response = send_file(path, mimetype=mimetype, conditional=False, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _android_apk_path():
    private_path = Path(current_app.config["ANDROID_APK_PATH"])
    if private_path.is_file():
        return private_path
    return Path(current_app.config["ANDROID_BUNDLED_APK_PATH"])


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
    write_audit(current_user, "logout", ip_address=request.remote_addr)
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("main.login"))


@bp.route("/admin")
@admin_required
def admin_dashboard():
    today = india_date(utcnow())
    rows = _live_rows(today)
    return render_template(
        "admin_dashboard.html",
        employee_count=User.query.filter_by(role="employee").count(),
        site_count=AssignedSite.query.filter_by(is_active=True).count(),
        onsite_count=Attendance.query.filter_by(status="onsite").count(),
        live_rows=rows,
        map_sites=AssignedSite.query.filter_by(is_active=True).order_by(AssignedSite.site_name).all(),
        **_leave_counts(india_date(utcnow())),
    )


@bp.route("/admin/metrics/<metric>")
@admin_required
def admin_metric(metric):
    today = india_date(utcnow())
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
                    "employee": ", ".join(a.employee.name for a in site.assignments if a.is_active) or "Unassigned",
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
                    "details": f"{visit.site.site_name} - {format_ist(visit.checkin_time)}",
                    "status": "Onsite",
                    "pdf_url": None,
                }
            )
    elif metric == "pending-reports":
        title = "Stopped visits - report pending"
        for visit in Attendance.query.filter_by(status="report_pending").order_by(Attendance.checkout_time.desc()):
            rows.append({
                "employee": visit.employee.name,
                "details": f"{visit.site.site_name} - Stopped {format_ist(visit.checkout_time)}",
                "status": "Auto-stopped; report pending" if visit.auto_closed else "Report pending",
                "pdf_url": None,
            })
    elif metric == "completed":
        title = "Completed today"
        visits = _date_filtered(
            Attendance.query.filter_by(status="completed"), today, today, INDIA_TIME
        ).order_by(Attendance.checkout_time.desc())
        for visit in visits:
            rows.append(
                {
                    "employee": visit.employee.name,
                    "details": f"{visit.site.site_name} - {format_ist(visit.checkout_time)}",
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
                    "details": f"{report.attendance.site.site_name} - Submitted {format_ist(report.report_date)}",
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
    today = india_date(utcnow())
    rows = _live_rows(today)
    return jsonify(
        **_leave_counts(india_date(utcnow())),
        onsite_count=sum(row["status"] == "onsite" for row in rows),
        pending_report_count=Attendance.query.filter_by(status="report_pending").count(),
        employees=[
            {
                "name": row["employee"].name,
                "designation": row["employee"].designation,
                "site": ", ".join(site.site_name for site in row["sites"]) or None,
                "customer": ", ".join(dict.fromkeys(site.customer_name for site in row["sites"])) or None,
                "checkin_time": format_ist(row["attendance"].checkin_time, "%H:%M IST")
                if row["attendance"]
                else None,
                "onsite_hours": row["onsite_hours"],
                "status": row["status"],
                "daily_statuses": row["daily_statuses"],
            }
            for row in rows
        ],
    )


def _employee_details(employee=None):
    details = {}
    for field, label, maximum in (
        ("address", "Address", 1000),
        ("emergency_contact_no", "Emergency contact number", 40),
        ("blood_group", "Blood group", 3),
        ("laptop_serial_no", "Laptop serial number", 120),
        ("employee_code", "Employee ID", 80),
    ):
        existing = getattr(employee, field, "") or ""
        value = request.form.get(field, existing).strip()
        if field in ("blood_group", "employee_code"):
            value = value.upper()
        if len(value) > maximum:
            raise ValueError(f"{label} must be at most {maximum} characters.")
        details[field] = value
    if details["blood_group"] and details["blood_group"] not in BLOOD_GROUPS:
        raise ValueError("Select a valid blood group or leave it unspecified.")
    if "birthday" in request.form:
        birthday = request.form["birthday"].strip()
        if birthday:
            try:
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", birthday):
                    raise ValueError
                details["birthday"] = date.fromisoformat(birthday)
            except ValueError:
                raise ValueError("Enter a valid birthday in YYYY-MM-DD format.") from None
            if details["birthday"] > india_date(utcnow()):
                raise ValueError("Birthday cannot be in the future.")
        else:
            details["birthday"] = None
    else:
        details["birthday"] = employee.birthday if employee is not None else None
    details["employee_code"] = details["employee_code"] or None
    if details["employee_code"]:
        duplicate = User.query.filter_by(employee_code=details["employee_code"])
        if employee is not None:
            duplicate = duplicate.filter(User.id != employee.id)
        if duplicate.first():
            raise ValueError("An account already uses that Employee ID.")
    return details


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
            try:
                details = _employee_details()
            except ValueError as error:
                flash(str(error), "danger")
            else:
                employee = User(
                    name=name, email=email, mobile=mobile,
                    designation=designation, role="employee", **details,
                )
                employee.set_password(password)
                db.session.add(employee)
                write_audit(current_user, "employee_created", email)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    current_app.logger.warning("Employee creation conflicted with an existing unique account field.")
                    flash("Email or Employee ID is already in use. Review the details and try again.", "danger")
                else:
                    flash("Employee created.", "success")
                    return redirect(url_for("main.employees"))
    records = User.query.filter_by(role="employee").order_by(User.name).all()
    return render_template(
        "employees.html", employees=records, blood_groups=BLOOD_GROUPS,
        birthday_max=india_date(utcnow()).isoformat(),
    )


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
        try:
            details = _employee_details(employee)
        except ValueError as error:
            flash(str(error), "danger")
        else:
            employee.name, employee.email = name, email
            employee.mobile, employee.designation = mobile, designation
            for field, value in details.items():
                setattr(employee, field, value)
            write_audit(current_user, "employee_updated", email)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                current_app.logger.warning("Employee update conflicted with an existing unique account field.")
                flash("Email or Employee ID is already in use. Review the details and try again.", "danger")
            else:
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
        or DailyWorkStatus.query.filter_by(employee_id=user_id).first()
        or MonthlyLeaveEntitlement.query.filter_by(employee_id=user_id).first()
        or db.session.get(LeaveAllowanceAccount, user_id)
        or SubmittedReport.query.filter_by(employee_id=user_id).first()
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
@bp.route("/admin/sites/<int:site_id>/edit", methods=["GET", "POST"])
@admin_required
def sites(site_id=None):
    editing_site = db.get_or_404(AssignedSite, site_id) if site_id is not None else None
    if editing_site and Attendance.query.filter_by(site_id=editing_site.id, status="onsite").first():
        flash("This site cannot be edited during an active visit. Other assignments can still be added or edited.", "danger")
        return redirect(url_for("main.sites"))
    values = request.form if request.method == "POST" else editing_site
    if request.method == "POST":
        try:
            latitude = float(request.form.get("latitude", ""))
            longitude = float(request.form.get("longitude", ""))
            radius = float(request.form.get("allowed_radius", "100"))
            details = [
                request.form.get(key, "").strip()
                for key in ("site_name", "customer_name", "address")
            ]
            project_id_value = request.form.get("project_id", "").strip()
            project = None
            if project_id_value:
                try:
                    project = Project.query.filter_by(
                        id=int(project_id_value)
                    ).first()
                except ValueError:
                    project = None
                if project is None:
                    raise ValueError
            signoff = _report_signoff_fields()
            if (
                not all(details)
                or len(details[0]) > 160 or len(details[1]) > 160
            ):
                raise ValueError
            if not (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
                and 10 <= radius <= 5000
            ):
                raise ValueError
        except (TypeError, ValueError):
            flash("Enter valid site details, project selection, coordinates and radius. Keep manager fields within their limits and remarks to five lines (2,000 characters).", "danger")
        else:
            if editing_site:
                site = editing_site
                site.site_name, site.customer_name, site.address = details
                site.latitude, site.longitude = latitude, longitude
                site.allowed_radius = radius
            else:
                site = AssignedSite(
                    site_name=details[0],
                    customer_name=details[1],
                    address=details[2],
                    latitude=latitude,
                    longitude=longitude,
                    allowed_radius=radius,
                )
                db.session.add(site)
            site.project = project
            site.project_name = project.name if project else ""
            site.report_company = REPORT_COMPANY_NAME
            for key, value in signoff.items():
                setattr(site, key, value)
            db.session.flush()
            write_audit(current_user, "site_updated" if editing_site else "site_created",
                        f"Site {site.id}: {site.site_name}")
            db.session.commit()
            flash("Site details saved. Use Assign sites to select engineers.", "success")
            return redirect(url_for("main.sites"))
    active_sites = AssignedSite.query.filter_by(is_active=True).order_by(AssignedSite.site_name).all()
    return render_template(
        "sites.html", sites=active_sites, editing_site=editing_site, values=values,
        projects=Project.query.order_by(Project.name).all(),
    )


@bp.route("/admin/projects", methods=["GET", "POST"])
@admin_required
def projects():
    if request.method == "POST":
        project_number = request.form.get("project_number", "").strip().upper()
        name = request.form.get("name", "").strip()
        if not project_number or len(project_number) > 80:
            flash("Enter a project number of at most 80 characters.", "danger")
        elif not name or len(name) > 250:
            flash("Enter a project name of at most 250 characters.", "danger")
        elif Project.query.filter(func.upper(Project.project_number) == project_number).first():
            flash("A project with that number already exists.", "danger")
        elif Project.query.filter(func.lower(Project.name) == name.lower()).first():
            flash("A project with that name already exists.", "danger")
        else:
            project = Project(project_number=project_number, name=name)
            db.session.add(project)
            write_audit(current_user, "project_created", f"{project_number}: {name}")
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("A project with that number or name already exists.", "danger")
                return redirect(url_for("main.projects"))
            flash("Project added.", "success")
            return redirect(url_for("main.projects"))
    return render_template(
        "projects.html",
        projects=Project.query.order_by(Project.name).all(),
        values=request.form if request.method == "POST" else None,
    )


@bp.route("/admin/projects/<int:project_id>/edit", methods=["POST"])
@admin_required
def edit_project(project_id):
    project = db.get_or_404(Project, project_id)
    project_number = request.form.get("project_number", project.project_number).strip().upper()
    name = request.form.get("name", "").strip()
    if not project_number or len(project_number) > 80:
        flash("Enter a project number of at most 80 characters.", "danger")
    elif not name or len(name) > 250:
        flash("Enter a project name of at most 250 characters.", "danger")
    elif Project.query.filter(
        func.upper(Project.project_number) == project_number, Project.id != project.id
    ).first():
        flash("A project with that number already exists.", "danger")
    elif Project.query.filter(
        func.lower(Project.name) == name.lower(), Project.id != project.id
    ).first():
        flash("A project with that name already exists.", "danger")
    else:
        project.project_number = project_number
        project.name = name
        for site in project.sites:
            site.project_name = name
        write_audit(current_user, "project_updated", f"{project_number}: {name}")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A project with that number or name already exists.", "danger")
            return redirect(url_for("main.projects"))
        flash("Project updated.", "success")
    return redirect(url_for("main.projects"))


@bp.route("/admin/projects/<int:project_id>/delete", methods=["POST"])
@admin_required
def delete_project(project_id):
    project = db.get_or_404(Project, project_id)
    if project.sites:
        flash("This project is assigned to one or more sites and cannot be deleted.", "danger")
    else:
        write_audit(current_user, "project_deleted", project.name)
        db.session.delete(project)
        db.session.commit()
        flash("Project deleted.", "success")
    return redirect(url_for("main.projects"))


@bp.route("/admin/assignments", methods=["GET", "POST"])
@admin_required
def site_assignments():
    employees = User.query.filter_by(role="employee", is_active=True).order_by(User.name).all()
    raw_id = request.form.get("employee_id") if request.method == "POST" else request.args.get("employee_id")
    employee = None
    if raw_id:
        try:
            employee_id = int(raw_id)
        except ValueError:
            abort(400, description="Select a valid engineer.")
        employee = User.query.filter_by(id=employee_id, role="employee", is_active=True).first_or_404()
    if request.method == "POST":
        if employee is None:
            abort(400, description="Select an engineer before assigning sites.")
        try:
            selected = {int(value) for value in request.form.getlist("site_ids")}
        except ValueError:
            abort(400, description="Select valid sites.")
        available = {site.id for site in AssignedSite.query.filter_by(is_active=True)}
        if not selected <= available:
            abort(400, description="Only active catalog sites can be assigned.")
        # Serialize changes for the same engineer and keep historical membership rows.
        db.session.query(User).filter_by(id=employee.id).with_for_update().one()
        existing = {a.site_id: a for a in SiteAssignment.query.filter_by(employee_id=employee.id)}
        removing = {site_id for site_id, a in existing.items() if a.is_active} - selected
        if removing and Attendance.query.filter(
            Attendance.employee_id == employee.id, Attendance.status == "onsite",
            Attendance.site_id.in_(removing),
        ).first():
            flash("Cannot unassign a site while this engineer has an active visit there. No assignments were changed.", "danger")
        else:
            for site_id, assignment in existing.items():
                assignment.is_active = site_id in selected
            for site_id in selected - existing.keys():
                db.session.add(SiteAssignment(employee_id=employee.id, site_id=site_id))
            write_audit(current_user, "site_assignments_updated",
                        f"Engineer {employee.id}; active sites: {sorted(selected)}")
            db.session.commit()
            flash("Engineer site assignments saved.", "success")
        return redirect(url_for("main.site_assignments", employee_id=employee.id))
    selected = {a.site_id for a in employee.site_assignments if a.is_active} if employee else set()
    return render_template(
        "assignments.html", employees=employees, selected_employee=employee,
        sites=AssignedSite.query.filter_by(is_active=True).order_by(AssignedSite.site_name).all(),
        selected_sites=selected,
    )


def _report_signoff_fields():
    fields = {
        key: request.form.get(key, "").strip()
        for key in ("commissioning_manager", "project_manager", "report_remarks")
    }
    if (
        len(fields["commissioning_manager"]) > 120
        or len(fields["project_manager"]) > 120
        or len(fields["report_remarks"]) > 2000
        or len(fields["report_remarks"].splitlines()) > 5
    ):
        raise ValueError("Enter manager details within the field limits and at most five remarks lines (2,000 characters).")
    return fields


@bp.route("/admin/sites/<int:site_id>/report-details", methods=["POST"])
@admin_required
def site_report_details(site_id):
    site = db.get_or_404(AssignedSite, site_id)
    try:
        project_id_value = request.form.get("project_id", "").strip()
        project = None
        if project_id_value:
            try:
                project = Project.query.filter_by(
                    id=int(project_id_value)
                ).first()
            except ValueError:
                project = None
            if project is None:
                raise ValueError
        signoff = _report_signoff_fields()
    except ValueError:
        flash("Select a valid project, enter manager names within 120 characters, and use at most five remarks lines (2,000 characters).", "danger")
    else:
        site.project = project
        site.project_name = project.name if project else ""
        site.report_company = REPORT_COMPANY_NAME
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
        or SubmittedReport.query.filter_by(site_id=site_id).first()
    ):
        flash("This site has attendance, invoice or submitted-report history and cannot be deleted.", "danger")
    else:
        write_audit(current_user, "site_deleted", site.site_name)
        db.session.delete(site)
        db.session.commit()
        flash("Site assignment deleted.", "success")
    return redirect(url_for("main.sites"))


@bp.route("/employee")
@employee_required
def employee_dashboard():
    today = india_date(utcnow())
    start, end = month_bounds(today)
    summary = attendance_summary(current_user, start, end)
    approved_today, full_day_leave = approved_leave_today(current_user.id, today)
    reports = (
        Attendance.query.filter_by(employee_id=current_user.id, status="completed")
        .order_by(Attendance.checkout_time.desc())
        .limit(10)
        .all()
    )
    for visit in reports:
        visit.duration_label = _format_hours(_attendance_hours(visit))
    return render_template(
        "employee_dashboard.html",
        sites=AssignedSite.query.filter_by(is_active=True).filter(
            AssignedSite.assignments.any(employee_id=current_user.id, is_active=True)
        ).all(),
        active_attendance=_active_attendance(),
        pending_visits=Attendance.query.filter_by(employee_id=current_user.id, status="report_pending")
        .order_by(Attendance.checkout_time.desc()).all(),
        reports=reports,
        monthly_summary=summary, month_start=start, month_end=end, today=today,
        today_confirmation=DailyWorkStatus.query.filter_by(employee_id=current_user.id, work_date=today).first(),
        approved_today=approved_today, full_day_leave=full_day_leave,
        today_leave_requests=LeaveRequest.query.filter(
            LeaveRequest.employee_id == current_user.id, LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today, LeaveRequest.status != "approved",
        ).order_by(LeaveRequest.id.desc()).all(),
        leave_balance=monthly_leave_balance(current_user, start),
    )


@bp.route("/employee/referrals", methods=["GET", "POST"])
@employee_required
def employee_referrals():
    if request.method == "POST":
        candidate_name = request.form.get("candidate_name", "").strip()
        candidate_email = request.form.get("candidate_email", "").strip()
        candidate_mobile = request.form.get("candidate_mobile", "").strip()
        notes = request.form.get("notes", "").strip()
        resume = request.files.get("resume")
        try:
            validate_email(candidate_email)
        except EmailNotValidError:
            flash("Enter a valid candidate email address.", "danger")
            return redirect(url_for("main.employee_referrals"))
        if not candidate_name or len(candidate_name) > 120:
            flash("Enter the candidate name.", "danger")
            return redirect(url_for("main.employee_referrals"))
        allowed_resume_extensions = {".pdf", ".doc", ".docx"}
        original_resume_name = secure_filename(resume.filename or "") if resume else ""
        resume_ext = Path(original_resume_name).suffix.lower()
        if not resume or not resume.filename or resume_ext not in allowed_resume_extensions:
            flash("Upload a PDF, DOC, or DOCX resume.", "danger")
            return redirect(url_for("main.employee_referrals"))
        resume.seek(0, os.SEEK_END)
        resume_size = resume.tell()
        resume.seek(0)
        if resume_size == 0 or resume_size > 5 * 1024 * 1024:
            flash("Resume must be non-empty and no larger than 5 MB.", "danger")
            return redirect(url_for("main.employee_referrals"))
        resume_folder = Path(current_app.config["UPLOAD_FOLDER"]) / "referral-resumes"
        resume_folder.mkdir(parents=True, exist_ok=True)
        stored_resume_name = f"{uuid.uuid4().hex}{resume_ext}"
        resume_path = resume_folder / stored_resume_name
        resume.save(resume_path)
        referral = CandidateReferral(
            employee_id=current_user.id, candidate_name=candidate_name,
            candidate_email=candidate_email, candidate_mobile=candidate_mobile,
            resume_filename=stored_resume_name,
            resume_original_filename=original_resume_name,
            notes=notes,
        )
        db.session.add(referral)
        db.session.flush()
        write_audit(current_user, "candidate_referred", candidate_name)
        settings = db.session.get(AppSettings, 1)
        recipient = settings.referral_notification_email if settings else ""
        db.session.commit()
        if recipient:
            try:
                send_email(current_app, recipient, "New candidate referral",
                            f"{current_user.name} referred {candidate_name}.\nEmail: {candidate_email}\nMobile: {candidate_mobile or '-'}\nNotes: {notes or '-'}",
                            attachments=[(original_resume_name, resume_path)])
            except (OSError, RuntimeError, smtplib.SMTPException) as error:
                current_app.logger.warning("Referral email failed: %r", error)
        flash("Candidate referred successfully.", "success")
        return redirect(url_for("main.employee_referrals"))
    referrals = CandidateReferral.query.filter_by(employee_id=current_user.id).order_by(CandidateReferral.created_at.desc()).all()
    return render_template("candidate_referrals.html", referrals=referrals)


@bp.route("/admin/referrals")
@admin_required
def admin_referrals():
    referrals = CandidateReferral.query.order_by(CandidateReferral.created_at.desc()).all()
    return render_template("admin_referrals.html", referrals=referrals)


@bp.route("/admin/referrals/<int:referral_id>/resume")
@login_required
def referral_resume(referral_id):
    referral = db.session.get(CandidateReferral, referral_id)
    if not referral:
        abort(404)
    if not current_user.is_admin and referral.employee_id != current_user.id:
        abort(403)
    if not referral.resume_filename:
        abort(404)
    path = Path(current_app.config["UPLOAD_FOLDER"])
    resume_path = path / "referral-resumes" / referral.resume_filename
    if not resume_path.is_file():
        abort(404)
    return send_file(resume_path, as_attachment=True, download_name=referral.resume_original_filename)


@bp.route("/employee/work-status", methods=["POST"])
@employee_required
def submit_work_status():
    _lock_employee(current_user.id)
    now = utcnow()
    today = india_date(now)
    if now >= daily_cutoff(now):
        flash("Today's confirmation deadline (23:59 IST) has passed.", "danger")
        return redirect(url_for("main.employee_dashboard"))
    work_type = request.form.get("work_type", "")
    reason = request.form.get("reason", "").strip()
    if _full_day_leave_block():
        flash("You are already on approved full-day leave today. No confirmation is needed.", "info")
        return redirect(url_for("main.employee_dashboard"))
    if work_type == "onsite":
        try:
            site_id = int(request.form.get("site_id", ""))
        except ValueError:
            flash("Select one of your assigned sites.", "danger")
            return redirect(url_for("main.employee_dashboard"))
        return start_visit(site_id)
    if work_type == "leave":
        return _request_employee_leave(today, today, request.form.get("period", "full"), reason, "main.employee_dashboard")
    if work_type not in ("wfh", "travelling", "training") or not 1 <= len(reason) <= 2000:
        flash("Select WFH, Travelling or Training and enter a reason of 1 to 2,000 characters.", "danger")
        return redirect(url_for("main.employee_dashboard"))
    lower = datetime.combine(today, datetime.min.time(), tzinfo=INDIA_TIME).astimezone(timezone.utc)
    if Attendance.query.filter(Attendance.employee_id == current_user.id, Attendance.checkin_time >= lower).first():
        flash("A site visit is already recorded today; no non-site confirmation is needed.", "warning")
        return redirect(url_for("main.employee_dashboard"))
    item = DailyWorkStatus(employee_id=current_user.id, work_date=today, work_type=work_type, reason=reason)
    db.session.add(item)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        if not DailyWorkStatus.query.filter_by(employee_id=current_user.id, work_date=today).first():
            raise
        flash("Today's work status has already been submitted.", "warning")
    else:
        write_audit(current_user, "work_status_submitted", f"{today}: {item.type_label}; reason={reason}")
        db.session.commit()
        flash("Work status submitted for administrator approval.", "success")
    return redirect(url_for("main.employee_dashboard"))


@bp.route("/admin/work-status")
@admin_required
def admin_work_status():
    today = india_date(utcnow())
    start, end = month_bounds(today)
    status = request.args.get("status", "all")
    if status not in ("all", "pending", "approved", "rejected"):
        abort(400)
    query = DailyWorkStatus.query.options(joinedload(DailyWorkStatus.employee))
    if status != "all":
        query = query.filter_by(status=status)
    pagination = query.order_by(DailyWorkStatus.work_date.desc(), DailyWorkStatus.id.desc()).paginate(
        per_page=30, error_out=False
    )
    employees = User.query.filter_by(role="employee").order_by(User.name).all()
    return render_template(
        "work_status.html", pagination=pagination, selected_status=status,
        month_start=start,
        employee_summaries=[(employee, attendance_summary(employee, start, end)) for employee in employees],
    )


@bp.route("/admin/work-status/<int:status_id>/review", methods=["POST"])
@admin_required
def review_work_status(status_id):
    item = db.get_or_404(DailyWorkStatus, status_id)
    decision = request.form.get("decision")
    if decision not in ("approved", "rejected"):
        abort(400)
    changed = DailyWorkStatus.query.filter_by(id=item.id, status="pending").update({
        "status": decision, "reviewed_by_id": current_user.id, "reviewed_at": utcnow(),
    })
    if changed:
        write_audit(current_user, f"work_status_{decision}", f"Confirmation {item.id}; {item.work_date}; {item.type_label}")
        db.session.commit()
        flash(f"Work status {decision}. Attendance totals now reflect this decision.", "success")
    else:
        flash("This work status has already been reviewed.", "warning")
    return redirect(url_for("main.admin_work_status"))


@bp.route("/admin/work-status/<int:status_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_work_status(status_id):
    item = db.get_or_404(DailyWorkStatus, status_id)
    values = {"work_type": item.work_type, "reason": item.reason}
    if request.method == "POST":
        _lock_employee(item.employee_id)
        values = {name: request.form.get(name, "").strip() for name in values}
        if values["work_type"] not in ("wfh", "training", "travelling") or not 1 <= len(values["reason"]) <= 2000:
            flash("Select a valid work status and enter a reason of 1 to 2,000 characters.", "danger")
            return render_template("edit_work_status.html", item=item, values=values), 400
        if (values["work_type"], values["reason"]) != (item.work_type, item.reason):
            previous = f"{item.work_type}; {item.status}; reason={item.reason}"
            item.work_type, item.reason = values["work_type"], values["reason"]
            item.status, item.reviewed_by_id, item.reviewed_at = "pending", None, None
            write_audit(current_user, "work_status_corrected",
                        f"Confirmation {item.id}; before={previous}; after={item.work_type}; reason={item.reason}; pending")
            db.session.commit()
            flash("Work confirmation corrected. Review its approval separately.", "success")
        else:
            flash("No work status changes to save.", "info")
        return redirect(url_for("main.admin_work_status"))
    return render_template("edit_work_status.html", item=item, values=values)


def _render_leaves(today, selected_status="all"):
    start_raw = request.args.get("from_date", today.isoformat()).strip()
    end_raw = request.args.get("to_date", today.isoformat()).strip()
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

    automatic_leave_rows = []
    policy = db.session.get(AttendancePolicy, 1)
    if policy:
        automatic_start = max(start, policy.effective_from) if start else policy.effective_from
        automatic_end = min(end or india_date(utcnow()), india_date(utcnow()))
        if automatic_start <= automatic_end:
            for employee in employees if current_user.is_admin else [current_user]:
                summary = attendance_summary(employee, automatic_start, automatic_end)
                totals[employee.id] = totals.get(employee.id, 0) + summary["totals"]["automatic"]
                automatic_leave_rows.extend((employee, item) for item in summary["automatic"])

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
    month_raw = request.args.get("leave_month", india_date(utcnow()).strftime("%Y-%m"))
    try:
        balance_month = date.fromisoformat(month_raw + "-01")
    except ValueError:
        abort(400, description="Choose a valid entitlement month.")
    return render_template(
        "leaves.html", today=today, selected_status=selected_status,
        leaves=query.order_by(LeaveRequest.created_at.desc()).all(),
        from_date=start_raw, to_date=end_raw, employee_search=employee_search,
        employee_id=employee_id, employee_options=User.query.filter_by(role="employee")
        .order_by(User.name, User.id).all() if current_user.is_admin else [],
        leave_total=sum(totals.values()),
        employee_totals=[(employee, totals.get(employee.id, 0)) for employee in employees],
        automatic_leave_rows=automatic_leave_rows,
        balance_month=balance_month,
        leave_balances=[(employee, monthly_leave_balance(employee, balance_month)) for employee in employees]
        if current_user.is_admin else [(current_user, monthly_leave_balance(current_user, balance_month))],
    )


def _validate_leave_details(start, end, period, reason):
    if end < start:
        raise ValueError("The end date must be on or after the start.")
    if period not in ("full", "morning", "afternoon"):
        raise ValueError("Choose full-day leave or a morning/afternoon half day.")
    if period != "full" and start != end:
        raise ValueError("Half-day leave must start and end on the same date.")
    if not 1 <= len(reason) <= 2000:
        raise ValueError("Enter a leave reason of 1 to 2,000 characters.")


def _request_employee_leave(start, end, period, reason, destination):
    _lock_employee(current_user.id)
    try:
        _validate_leave_details(start, end, period, reason)
    except ValueError as error:
        flash(f"Invalid leave request. {error}", "danger")
        return redirect(url_for(destination))
    if leave_overlap(current_user.id, start, end, period):
        flash("These dates overlap an existing pending or approved leave request.", "danger")
    elif _active_attendance() and start <= india_date(utcnow()) <= end:
        flash("Stop your active site before requesting leave for today.", "warning")
    else:
        db.session.add(LeaveRequest(
            employee_id=current_user.id, start_date=start, end_date=end, period=period, reason=reason,
        ))
        write_audit(current_user, "leave_requested", f"{start} to {end} ({period}); reason={reason}")
        db.session.commit()
        flash("Leave request submitted for administrator approval.", "success")
    return redirect(url_for(destination))


@bp.route("/admin/leaves/new", methods=["GET", "POST"])
@bp.route("/admin/leaves/<int:leave_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_leave_form(leave_id=None):
    leave = db.get_or_404(LeaveRequest, leave_id) if leave_id is not None else None
    employees = User.query.filter_by(role="employee").order_by(User.name).all()
    values = {
        "employee_id": str(leave.employee_id) if leave else "",
        "start_date": leave.start_date.isoformat() if leave else india_date(utcnow()).isoformat(),
        "end_date": leave.end_date.isoformat() if leave else india_date(utcnow()).isoformat(),
        "period": leave.period if leave else "full",
        "reason": leave.reason if leave else "",
    }
    if request.method == "POST":
        values = {name: request.form.get(name, "").strip() for name in values}
        try:
            employee_id = int(values["employee_id"])
            employee = _lock_employee(employee_id)
            if employee.role != "employee" or (leave and employee_id != leave.employee_id):
                raise ValueError("Select the employee who owns this leave.")
            start = date.fromisoformat(values["start_date"])
            end = date.fromisoformat(values["end_date"])
            _validate_leave_details(start, end, values["period"], values["reason"])
            if leave_overlap(employee_id, start, end, values["period"], leave.id if leave else None):
                raise ValueError("These dates overlap an existing pending or approved leave request.")
        except ValueError as error:
            flash(f"Invalid leave request. {error}", "danger")
            return render_template("admin_leave_form.html", leave=leave, employees=employees, values=values), 400
        previous = (
            f"{leave.start_date} to {leave.end_date}; {leave.period}; {leave.status}; reason={leave.reason}"
            if leave else "new record"
        )
        if leave and (start, end, values["period"], values["reason"]) == (
            leave.start_date, leave.end_date, leave.period, leave.reason
        ):
            flash("No leave changes to save.", "info")
        else:
            if leave is None:
                leave = LeaveRequest(employee_id=employee_id)
                db.session.add(leave)
            leave.start_date, leave.end_date = start, end
            leave.period, leave.reason = values["period"], values["reason"]
            leave.status, leave.reviewed_by_id, leave.reviewed_at = "pending", None, None
            db.session.flush()
            write_audit(current_user, "leave_details_saved",
                        f"Leave {leave.id}; employee {employee_id}; before={previous}; "
                        f"after={start} to {end}; {leave.period}; pending; reason={leave.reason}")
            db.session.commit()
            flash("Leave saved as pending. Approve or reject it separately.", "success")
        return redirect(url_for("main.admin_leaves", employee_id=employee_id))
    return render_template("admin_leave_form.html", leave=leave, employees=employees, values=values)


@bp.route("/admin/leaves/entitlement", methods=["POST"])
@admin_required
def save_leave_entitlement():
    try:
        employee_id = int(request.form.get("employee_id", ""))
        employee = _lock_employee(employee_id)
        if employee.role != "employee":
            raise ValueError("Select a valid employee.")
        month = date.fromisoformat(request.form.get("month", "") + "-01")
        account = db.session.get(LeaveAllowanceAccount, employee_id)
        joining = account.joining_date if account else india_date(employee.created_date)
        if month < joining.replace(day=1):
            raise ValueError("The rate cannot begin before the employee's joining month.")
        days = Decimal(request.form.get("days", ""))
        if not days.is_finite() or not 0 <= days <= 31 or days * 2 != (days * 2).to_integral_value():
            raise ValueError("Entitlement must be 0 to 31 days in half-day increments.")
    except (ValueError, InvalidOperation) as error:
        flash(f"Invalid monthly entitlement. {error}", "danger")
        return redirect(url_for("main.admin_leaves"))
    item = MonthlyLeaveEntitlement.query.filter_by(employee_id=employee_id, month=month).first()
    old_days = item.days if item else "not set"
    if item is None:
        item = MonthlyLeaveEntitlement(employee_id=employee_id, month=month)
        db.session.add(item)
    item.days, item.updated_by_id, item.updated_at = days, current_user.id, utcnow()
    write_audit(current_user, "leave_entitlement_updated",
                f"Employee {employee_id}; {month:%Y-%m}; {old_days} -> {days} days")
    db.session.commit()
    flash("Monthly leave rate saved from the selected month onward. Earlier credits are preserved.", "success")
    return redirect(url_for("main.admin_leaves", leave_month=month.strftime("%Y-%m"), employee_id=employee_id))


@bp.route("/admin/leaves/account", methods=["POST"])
@admin_required
def save_leave_account():
    try:
        employee_id = int(request.form.get("employee_id", ""))
        employee = _lock_employee(employee_id)
        if employee.role != "employee":
            raise ValueError("Select a valid employee.")
        joining = date.fromisoformat(request.form.get("joining_date", ""))
        if joining > india_date(utcnow()):
            raise ValueError("Joining date cannot be in the future.")
        opening = Decimal(request.form.get("opening_days", ""))
        if not opening.is_finite() or not 0 <= opening <= 999999 or opening * 2 != (opening * 2).to_integral_value():
            raise ValueError("Opening balance must be 0 to 999,999 days in half-day increments.")
        earliest = MonthlyLeaveEntitlement.query.filter_by(employee_id=employee_id).order_by(
            MonthlyLeaveEntitlement.month
        ).first()
        if earliest and earliest.month < joining.replace(day=1):
            raise ValueError("Joining date cannot exclude an existing monthly rate change.")
    except (ValueError, InvalidOperation) as error:
        flash(f"Invalid leave account. {error}", "danger")
        return redirect(url_for("main.admin_leaves"))
    account = db.session.get(LeaveAllowanceAccount, employee_id)
    previous = f"{account.joining_date}; {account.opening_days}" if account else "account creation date; 0"
    if account is None:
        account = LeaveAllowanceAccount(employee_id=employee_id)
        db.session.add(account)
    account.joining_date, account.opening_days = joining, opening
    account.updated_by_id, account.updated_at = current_user.id, utcnow()
    write_audit(current_user, "leave_account_updated",
                f"Employee {employee_id}; before={previous}; after={joining}; opening={opening}")
    db.session.commit()
    flash("Joining date and opening leave balance saved. Leave totals have been recalculated.", "success")
    return redirect(url_for("main.admin_leaves", employee_id=employee_id))


@bp.route("/employee/leaves", methods=["GET", "POST"])
@employee_required
def employee_leaves():
    today = india_date(utcnow())
    if request.method == "POST":
        period = request.form.get("period", "")
        reason = request.form.get("reason", "").strip()
        try:
            start = date.fromisoformat(request.form.get("start_date", ""))
            end = date.fromisoformat(request.form.get("end_date", ""))
            if start < today or end < start:
                raise ValueError("Choose dates from today onwards, with the end on or after the start.")
            _validate_leave_details(start, end, period, reason)
        except ValueError as error:
            flash(f"Invalid leave request. {error}", "danger")
        else:
            return _request_employee_leave(start, end, period, reason, "main.employee_leaves")
    return _render_leaves(today)


@bp.route("/admin/leaves")
@admin_required
def admin_leaves():
    return _render_leaves(india_date(utcnow()), request.args.get("status", "all"))


@bp.route("/admin/leaves/<int:leave_id>/review", methods=["POST"])
@admin_required
def review_leave(leave_id):
    leave = db.get_or_404(LeaveRequest, leave_id)
    _lock_employee(leave.employee_id)
    decision = request.form.get("decision")
    if decision not in ("approved", "rejected"):
        abort(400)
    if decision == "approved" and not leave.employee.is_active:
        flash("Requests from inactive employees cannot be approved.", "danger")
    elif decision == "approved" and leave_overlap(
        leave.employee_id, leave.start_date, leave.end_date, leave.period, leave.id, statuses=("approved",)
    ):
        flash("This leave overlaps another approved leave. Edit the dates or duration first.", "danger")
    elif decision == "approved" and leave.start_date <= india_date(utcnow()) <= leave.end_date and (
        leave.period == "full" or approved_leave_today(leave.employee_id, india_date(utcnow()))[0]
    ) and Attendance.query.filter_by(employee_id=leave.employee_id, status="onsite").first():
        flash("Stop the employee's active site before approving full-day leave today.", "danger")
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
    blocked = _full_day_leave_block()
    if blocked:
        flash(blocked, "warning")
        return redirect(url_for("main.employee_dashboard"))
    if _active_attendance():
        flash("Finish your current visit before starting another.", "warning")
        return redirect(url_for("main.employee_dashboard"))
    session.pop("visit_verification", None)
    return redirect(url_for("main.checkin_location", site_id=site.id))


@bp.route("/visit/<int:site_id>/checkin-location")
@employee_required
def checkin_location(site_id):
    site = _employee_site(site_id)
    blocked = _full_day_leave_block()
    if blocked:
        flash(blocked, "warning")
        return redirect(url_for("main.employee_dashboard"))
    if _active_attendance():
        return redirect(url_for("main.employee_dashboard"))
    return render_template("location.html", site=site, flow="checkin")


@bp.route("/api/visit/<int:site_id>/verify-checkin", methods=["POST"])
@employee_required
def verify_checkin(site_id):
    site = _employee_site(site_id)
    blocked = _full_day_leave_block()
    if blocked:
        return jsonify(error=blocked), 409
    if _active_attendance():
        return jsonify(error="Finish your current visit before starting another."), 409
    now = utcnow()
    if now >= daily_cutoff(now):
        return jsonify(error="Daily cutoff reached. Start a new visit after midnight India time."), 409
    latitude, longitude, error = _read_coordinates()
    if error:
        return error
    distance = haversine_distance(site.latitude, site.longitude, latitude, longitude)
    session["visit_verification"] = {
        "flow": "checkin",
        "site_id": site.id,
        "latitude": latitude,
        "longitude": longitude,
        "verified_at": now.isoformat(),
    }
    return jsonify(
        verified=True,
        distance=round(distance, 1),
        within_radius=distance <= site.allowed_radius,
        allowed=site.allowed_radius,
        next=url_for("main.checkin_camera", site_id=site.id),
    )


@bp.route("/visit/<int:site_id>/camera")
@employee_required
def checkin_camera(site_id):
    site = _employee_site(site_id)
    blocked = _full_day_leave_block()
    if blocked:
        abort(403, description=blocked)
    verification = session.get("visit_verification", {})
    if not _valid_verification(verification, "checkin", "site_id", site.id) or _active_attendance():
        abort(403)
    return render_template("camera.html", site=site)


@bp.route("/visit/<int:site_id>/checkin", methods=["POST"])
@employee_required
def complete_checkin(site_id):
    site = _employee_site(site_id)
    _lock_employee(current_user.id)
    blocked = _full_day_leave_block()
    if blocked:
        session.pop("visit_verification", None)
        flash(blocked, "warning")
        return redirect(url_for("main.employee_dashboard"))
    verification = session.get("visit_verification", {})
    if (
        not _valid_verification(verification, "checkin", "site_id", site.id)
        or _active_attendance()
    ):
        abort(403)
    now = utcnow()
    if now >= daily_cutoff(now):
        flash("Daily cutoff reached. Start a new visit after midnight India time.", "warning")
        return redirect(url_for("main.employee_dashboard"))
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
        checkin_time=now,
        site_latitude=site.latitude,
        site_longitude=site.longitude,
        site_radius_m=site.allowed_radius,
        checkin_distance_m=haversine_distance(
            site.latitude, site.longitude, verification["latitude"], verification["longitude"]
        ),
        checkin_location_time=datetime.fromisoformat(verification["verified_at"]),
        last_location_lat=verification["latitude"],
        last_location_lon=verification["longitude"],
        last_location_time=datetime.fromisoformat(verification["verified_at"]),
        checkin_selfie=selfie,
        status="onsite",
    )
    db.session.add(attendance)
    db.session.flush()
    write_audit(current_user, "site_checkin", f"Attendance {attendance.id}")
    db.session.commit()
    session.pop("visit_verification", None)
    flash("Site visit started. Your captured location and distance are recorded for the administrator.", "success")
    return redirect(url_for("main.employee_dashboard"))


@bp.route("/visit/<int:attendance_id>/checkout-location")
@employee_required
def checkout_location(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    if attendance.status == "report_pending":
        return redirect(url_for("main.site_report_form", attendance_id=attendance.id))
    if attendance.status != "onsite":
        abort(404)
    return render_template(
        "location.html", site=attendance.site, flow="checkout", attendance=attendance
    )


@bp.route("/api/visit/<int:attendance_id>/verify-checkout", methods=["POST"])
@employee_required
def verify_checkout(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    if attendance.status == "report_pending":
        return jsonify(
            verified=True,
            distance=round(attendance.checkout_distance_m, 1) if attendance.checkout_distance_m is not None else None,
            location_source=attendance.checkout_location_source,
            next=url_for("main.site_report_form", attendance_id=attendance.id),
        )
    if attendance.status != "onsite":
        abort(404)
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error="A JSON object is required."), 400
    coordinates = None
    reason = None
    if data.get("location_unavailable") is True:
        reason = data.get("reason")
        if reason not in ("permission_denied", "position_unavailable", "timeout", "unsupported", "insecure_context"):
            return jsonify(error="A valid location-unavailable reason is required."), 400
    else:
        latitude, longitude, error = _read_coordinates()
        if error:
            return error
        coordinates = (latitude, longitude)
    if not stop_attendance(attendance, utcnow(), coordinates=coordinates, reason=reason):
        return jsonify(error="This visit has already stopped. Refresh your dashboard."), 409
    db.session.commit()
    distance = attendance.checkout_distance_m
    radius = attendance.site_radius_m
    return jsonify(
        verified=True,
        distance=round(distance, 1) if distance is not None else None,
        within_radius=(distance <= radius) if distance is not None and radius is not None else True,
        location_source=attendance.checkout_location_source,
        next=url_for("main.site_report_form", attendance_id=attendance.id),
    )


@bp.route("/api/visit/<int:attendance_id>/location", methods=["POST"])
@employee_required
def capture_visit_location(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    latitude, longitude, error = _read_coordinates()
    if error:
        return error
    changed = Attendance.query.filter_by(id=attendance.id, status="onsite").update({
        "last_location_lat": latitude, "last_location_lon": longitude,
        "last_location_time": utcnow(),
    })
    if not changed:
        return jsonify(error="This visit has stopped.", active=False), 409
    db.session.commit()
    reference_lat = attendance.site_latitude if attendance.site_latitude is not None else attendance.site.latitude
    reference_lon = attendance.site_longitude if attendance.site_longitude is not None else attendance.site.longitude
    radius = attendance.site_radius_m if attendance.site_radius_m is not None else attendance.site.allowed_radius
    distance = haversine_distance(reference_lat, reference_lon, latitude, longitude)
    within_radius = distance <= radius
    return jsonify(recorded=True, distance=round(distance, 1), within_radius=within_radius,
                   radius_m=radius)


def _visit_monitor_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="site-reminder-monitor")


def _monitor_response(attendance, include_token=False):
    if not attendance or attendance.status != "onsite":
        response = jsonify(active=False)
    else:
        cutoff = daily_cutoff(attendance.checkin_time)
        if utcnow() >= cutoff:
            stop_attendance(attendance, utcnow(), automatic=True, reason="daily_cutoff")
            db.session.commit()
            response = jsonify(active=False)
        else:
            values = {
                "active": True, "attendance_id": attendance.id,
                "latitude": attendance.site_latitude if attendance.site_latitude is not None else attendance.site.latitude,
                "longitude": attendance.site_longitude if attendance.site_longitude is not None else attendance.site.longitude,
                "radius_m": attendance.site_radius_m if attendance.site_radius_m is not None else attendance.site.allowed_radius,
                "cutoff_time": cutoff.isoformat(),
            }
            if include_token:
                values["monitoring_token"] = _visit_monitor_serializer().dumps({
                    "visit": attendance.id, "cutoff": cutoff.isoformat(),
                })
            response = jsonify(values)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/api/employee/active-visit")
@employee_required
def active_visit_monitor():
    return _monitor_response(_active_attendance(), include_token=True)


@bp.route("/api/visit-monitor")
def visit_monitor():
    authorization = request.headers.get("Authorization", "")
    try:
        if not authorization.startswith("Bearer "):
            raise BadSignature("A monitoring token is required.")
        values = _visit_monitor_serializer().loads(authorization[7:], max_age=86400)
        cutoff = datetime.fromisoformat(values["cutoff"])
        if cutoff.tzinfo is None or utcnow() >= cutoff:
            raise BadSignature("Monitoring token expired.")
        attendance = db.session.get(Attendance, int(values["visit"]))
        if not attendance or not attendance.employee.is_active or cutoff != daily_cutoff(attendance.checkin_time):
            raise BadSignature("Invalid visit.")
    except (BadSignature, KeyError, TypeError, ValueError):
        response = jsonify(error="Invalid or expired visit-monitoring token.")
        response.headers["Cache-Control"] = "private, no-store"
        return response, 401
    return _monitor_response(attendance)


@bp.route("/visit/<int:attendance_id>/report", methods=["GET", "POST"])
@employee_required
def site_report_form(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    if attendance.status != "report_pending" or attendance.report:
        abort(403)
    if request.method == "POST":
        names = ("activity_detail", "billing", "engineer_name")
        values = {name: request.form.get(name, "").strip() for name in names}
        if not all(values.values()) or values["billing"] not in ("billable", "non_billable"):
            flash("Enter activity detail, select Billable or Non-billable, and enter the engineer name.", "danger")
            return render_template("site_report.html", attendance=attendance, values=values)
        changed = Attendance.query.filter_by(id=attendance.id, status="report_pending").update({
            "status": "completed",
        })
        if not changed:
            abort(409)
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
        write_audit(current_user, "site_report_submitted", f"Attendance {attendance.id}")
        db.session.commit()
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
        sites_query = sites_query.filter(_site_history_access(current_user.id))
    activity_sites = sites_query.order_by(AssignedSite.site_name).all()
    site_employee_ids = {
        site.id: sorted(
            {a.employee_id for a in site.assignments}
            | {visit.employee_id for visit in site.attendance_records}
        ) for site in activity_sites
    }
    return render_template(
        "reports.html", reports=records,
        activity_sites=activity_sites, site_employee_ids=site_employee_ids,
        activity_employees=User.query.filter_by(role="employee").order_by(User.name).all()
        if current_user.is_admin else [],
    )


def _submitted_report_for_user(report_id):
    query = SubmittedReport.query.filter_by(id=report_id)
    if not current_user.is_admin:
        query = query.filter_by(employee_id=current_user.id)
    return query.first_or_404()


@bp.route("/submitted-reports")
@login_required
def submitted_reports():
    query = SubmittedReport.query.options(
        joinedload(SubmittedReport.employee), joinedload(SubmittedReport.site),
        selectinload(SubmittedReport.versions),
    )
    if not current_user.is_admin:
        query = query.filter_by(employee_id=current_user.id)
    pagination = query.order_by(SubmittedReport.created_at.desc(), SubmittedReport.id.desc()).paginate(
        per_page=20, error_out=False
    )
    return render_template("submitted_reports.html", pagination=pagination)


@bp.route("/employee/submitted-reports/new", methods=["GET", "POST"])
@bp.route("/employee/submitted-reports/<int:report_id>/edit", methods=["GET", "POST"])
@employee_required
def submitted_report_form(report_id=None):
    report = _submitted_report_for_user(report_id) if report_id is not None else None
    current = report.current_version if report else None
    values = {
        "site_id": str(report.site_id) if report and report.site_id else "",
        "start_date": current.start_date.isoformat() if current else india_date(utcnow()).isoformat(),
        "end_date": current.end_date.isoformat() if current else india_date(utcnow()).isoformat(),
        "total_mandays": current.total_mandays if current else "",
        "revision_number": report.revision_number if report else "",
    }
    report_sites = AssignedSite.query.filter_by(is_active=True).filter(
        AssignedSite.assignments.any(employee_id=current_user.id, is_active=True)
    ).order_by(AssignedSite.site_name).all()
    assigned_site_ids = {site.id for site in report_sites}
    if report and report.site and all(site.id != report.site.id for site in report_sites):
        report_sites.append(report.site)

    def render_form(status=200):
        return render_template(
            "submitted_report_form.html", report=report, values=values, sites=report_sites,
            assigned_site_ids=assigned_site_ids,
        ), status

    if request.method == "GET":
        return render_form()
    values = {name: request.form.get(name, "").strip() for name in values}
    try:
        try:
            site_id = int(values["site_id"])
        except ValueError:
            raise ValueError("Select an active site assigned to you.") from None
        site = AssignedSite.query.filter_by(id=site_id, is_active=True).filter(
            AssignedSite.assignments.any(employee_id=current_user.id, is_active=True)
        ).first()
        if site is None and report and report.site_id == site_id:
            site = report.site
        if site is None:
            raise ValueError("Select an active site assigned to you.")
        start = date.fromisoformat(values["start_date"])
        end = date.fromisoformat(values["end_date"])
        if end < start:
            raise ValueError("The end date must be on or after the start date.")
        if not values["total_mandays"].isascii() or not values["total_mandays"].isdecimal():
            raise ValueError("Enter total man-days as a positive whole number.")
        total_mandays = int(values["total_mandays"])
        if not 1 <= total_mandays <= 2147483647:
            raise ValueError("Total man-days must be between 1 and 2,147,483,647.")
        if report and int(values["revision_number"]) != report.revision_number:
            flash("This report has changed. Reload the page before editing it again.", "warning")
            return render_form(409)
    except ValueError as error:
        flash(f"Invalid report details. {error}", "danger")
        return render_form(400)

    upload = request.files.get("pdf")
    filename = None
    if upload and upload.filename:
        try:
            filename, original_filename = save_report_pdf(upload, current_app.config["UPLOAD_FOLDER"])
        except ValueError as error:
            flash(str(error), "danger")
            return render_form(400)
        except OSError:
            current_app.logger.exception("Unable to store a submitted report PDF.")
            flash("The PDF could not be saved. Please try again.", "danger")
            return render_form(500)
    elif current:
        original_filename = current.original_filename
    else:
        flash("Choose a PDF report to submit.", "danger")
        return render_form(400)

    if current and not filename and (
        start, end, total_mandays
    ) == (current.start_date, current.end_date, current.total_mandays) and report.site_id == site.id:
        flash("No report changes to save.", "info")
        return redirect(url_for("main.submitted_report_detail", report_id=report.id))

    committed = False
    try:
        if report is None:
            report = SubmittedReport(
                employee_id=current_user.id, site=site, revision_number=1
            )
            db.session.add(report)
        else:
            report.site = site
            report.revision_number += 1
        version = SubmittedReportVersion(
            report=report, revision_number=report.revision_number,
            start_date=start, end_date=end, total_mandays=total_mandays,
            filename=filename or current.filename, original_filename=original_filename,
            pdf_replaced=filename is not None,
        )
        db.session.add(version)
        db.session.flush()
        if current is None:
            action = "report_submitted"
        elif filename:
            action = "report_pdf_replaced"
        else:
            action = "report_details_updated"
        write_audit(
            current_user, action,
            f"Submitted report {report.id}; version {version.revision_number}; "
            f"site={site.site_name}; {start} to {end}; "
            f"man-days={total_mandays}; file={original_filename}",
            ip_address=request.remote_addr,
        )
        db.session.commit()
        committed = True
    except StaleDataError:
        db.session.rollback()
        flash("This report was updated elsewhere. Reload the page before editing it again.", "warning")
        return render_form(409)
    except SQLAlchemyError:
        db.session.rollback()
        raise
    finally:
        if filename and not committed:
            (Path(current_app.config["UPLOAD_FOLDER"]) / filename).unlink(missing_ok=True)
    flash("Report submitted." if current is None else "Report updated. Previous versions have been retained.", "success")
    return redirect(url_for("main.submitted_report_detail", report_id=report.id))


@bp.route("/submitted-reports/<int:report_id>")
@login_required
def submitted_report_detail(report_id):
    return render_template("submitted_report_detail.html", report=_submitted_report_for_user(report_id))


@bp.route("/submitted-reports/<int:report_id>/versions/<int:version_id>/download")
@login_required
def download_submitted_report(report_id, version_id):
    report = _submitted_report_for_user(report_id)
    version = SubmittedReportVersion.query.filter_by(id=version_id, report_id=report.id).first_or_404()
    path = Path(current_app.config["UPLOAD_FOLDER"]) / version.filename
    if not path.is_file():
        current_app.logger.error("Missing PDF for submitted report %s, version %s.", report.id, version.id)
        abort(404, description="The report PDF is unavailable. Please contact the administrator.")
    response = send_file(
        path, mimetype="application/pdf", as_attachment=True,
        download_name=version.original_filename, max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _site_history_access(employee_id):
    return or_(
        AssignedSite.assignments.any(employee_id=employee_id),
        AssignedSite.attendance_records.any(employee_id=employee_id),
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
    else:
        employee_id = current_user.id
    employee = User.query.filter_by(id=employee_id, role="employee").first_or_404()
    site = query.filter(_site_history_access(employee.id)).first_or_404()
    visits = _date_filtered(
        Attendance.query.filter_by(site_id=site.id, employee_id=employee.id, status="completed")
        .filter(Attendance.report.has()), start, end, tz=INDIA_TIME
    ).order_by(Attendance.checkout_time, Attendance.id).all()
    leaves = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee.id,
        LeaveRequest.status == "approved", LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    ).order_by(LeaveRequest.id).all()
    from .activity_report import activity_workbook

    output = activity_workbook(
        site, visits, leaves, start, end, employee,
        automatic_leave=attendance_summary(employee, start, end)["automatic"],
    )
    return send_file(
        output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name=f"site-activity-{site.id}-{start}-{end}.xlsx",
    )


@bp.route("/reports/combined.pdf")
@admin_required
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
    records = _date_filtered(query, start_date, end_date, INDIA_TIME).order_by(
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
@admin_required
def download_pdf(attendance_id):
    attendance = _attendance_for_user(attendance_id)
    if attendance.status != "completed" or not attendance.report:
        abort(404)
    return _pdf_response(attendance)


@bp.route("/admin/invoices", methods=["GET", "POST"])
@admin_required
def invoices():
    if request.method == "POST" and request.form.get("form_action") == "create_po":
        try:
            po_number = request.form.get("po_number", "").strip()
            customer_name = request.form.get("po_customer_name", "").strip()
            employee_id = int(request.form.get("po_employee_id", ""))
            site_id = int(request.form.get("po_site_id", ""))
            start_date = date.fromisoformat(request.form.get("po_start_date", ""))
            end_date = date.fromisoformat(request.form.get("po_end_date", ""))
            total_mandays = int(request.form.get("total_mandays", "0"))
            if not po_number or not customer_name:
                raise ValueError("Enter the PO number and customer name.")
            if end_date < start_date:
                raise ValueError("PO end date must be on or after the start date.")
            if not 0 <= total_mandays <= 2147483647:
                raise ValueError("Man-days must be a whole number of zero or more.")
            if PurchaseOrder.query.filter_by(po_number=po_number).first():
                raise ValueError("That PO number already exists.")
            site = AssignedSite.query.filter_by(id=site_id, is_active=True).first()
            employee = User.query.filter_by(id=employee_id, role="employee", is_active=True).first()
            if not site or not employee or not SiteAssignment.query.filter_by(employee_id=employee_id, site_id=site_id, is_active=True).first():
                raise ValueError("Select an active employee assigned to the selected site.")
        except (TypeError, ValueError) as error:
            flash(str(error) or "Complete all PO fields.", "danger")
        else:
            po = PurchaseOrder(po_number=po_number, customer_name=customer_name, employee_id=employee_id,
                               site_id=site_id, start_date=start_date, end_date=end_date,
                               total_mandays=total_mandays)
            db.session.add(po)
            write_audit(current_user, "purchase_order_created", f"{po_number}: {total_mandays} man-days")
            db.session.commit()
            flash(f"PO {po_number} saved.", "success")
        return redirect(url_for("main.invoices"))
    if request.method == "POST":
        try:
            site_id = int(request.form.get("site_id", ""))
            employee_id = int(request.form.get("employee_id", ""))
            site = AssignedSite.query.filter_by(id=site_id, is_active=True).first()
            assignment = SiteAssignment.query.filter_by(
                site_id=site_id, employee_id=employee_id, is_active=True
            ).first()
            if not site or not assignment or not assignment.employee.is_active:
                raise ValueError("Select an active engineer and one of their assigned sites.")
            po_number = request.form.get("po_number", "").strip()
            raw_po_date = request.form.get("po_date", "").strip()
            po_date = date.fromisoformat(raw_po_date) if raw_po_date else None
            if len(po_number) > 120:
                raise ValueError("PO number must be at most 120 characters.")
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
                employee_id=employee_id,
                site_id=site.id,
                po_number=po_number, po_date=po_date,
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
    site_options = SiteAssignment.query.join(SiteAssignment.site).join(SiteAssignment.employee).filter(
        SiteAssignment.is_active.is_(True), AssignedSite.is_active.is_(True), User.is_active.is_(True),
    ).order_by(User.name, AssignedSite.site_name).all()
    invoice_records = Invoice.query.order_by(Invoice.created_at.desc()).limit(500).all()
    purchase_orders = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).all()
    settings = db.session.get(AppSettings, 1)
    changed = False
    for po in purchase_orders:
        used = _used_po_mandays(po)
        if po.used_mandays != used:
            po.used_mandays = used
            changed = True
        generated = Invoice.query.filter_by(po_number=po.po_number).first() is not None
        if po.invoice_generated != generated:
            po.invoice_generated = generated
            changed = True
        if po.total_mandays > 0 and used >= po.total_mandays and not po.completion_email_sent and settings and settings.po_notification_email:
            try:
                send_email(current_app, settings.po_notification_email, f"PO {po.po_number} man-days completed",
                           f"PO {po.po_number} for {po.customer_name} has used {used} of {po.total_mandays} allocated man-days. Please review pending invoice and payment status.")
                po.completion_email_sent = True
                changed = True
            except (OSError, RuntimeError, smtplib.SMTPException) as error:
                current_app.logger.warning("PO completion email failed: %r", error)
    if changed:
        db.session.commit()
    return render_template("invoices.html", assignments=site_options, invoices=invoice_records,
                           purchase_orders=purchase_orders)


@bp.route("/admin/purchase-orders/<int:po_id>/status", methods=["POST"])
@admin_required
def update_purchase_order_status(po_id):
    po = db.session.get(PurchaseOrder, po_id)
    if not po:
        abort(404)
    if "invoice_generated" in request.form:
        po.invoice_generated = request.form.get("invoice_generated") == "yes"
    if "money_received" in request.form:
        po.money_received = request.form.get("money_received") == "yes"
    db.session.commit()
    flash(f"PO {po.po_number} status updated.", "success")
    return redirect(url_for("main.invoices"))


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
    now = utcnow()
    return render_template(
        "attendance.html", attendance=visits, audit_logs=logs,
        visit_durations={visit.id: _format_hours(_attendance_hours(visit, now)) for visit in visits},
    )


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
