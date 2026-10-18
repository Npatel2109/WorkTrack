import io
import os
import smtplib
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path

import pandas as pd
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
from .models import AssignedSite, Attendance, AuditLog, Invoice, SiteReport, User, utcnow
from .services import (
    generate_invoice_pdf,
    generate_site_report_pdf,
    haversine_distance,
    merge_site_reports_pdf,
    save_data_image,
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


def _live_rows(today):
    rows = []
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
    return render_template("login.html")


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
                current_app.config["PUBLIC_BASE_URL"].rstrip("/")
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
    rows = _live_rows(datetime.now(timezone.utc).date())
    return jsonify(
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
            if not employee or not all(details):
                raise ValueError
            if not (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
                and 10 <= radius <= 5000
            ):
                raise ValueError
        except (TypeError, ValueError):
            flash("Enter valid site details, coordinates, radius, and employee.", "danger")
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
            write_audit(current_user, "site_assigned", f"{site.site_name} -> {employee.email}")
            db.session.commit()
            flash("Site assignment saved.", "success")
            return redirect(url_for("main.sites"))
    active_sites = AssignedSite.query.filter_by(is_active=True).order_by(AssignedSite.site_name).all()
    employee_options = User.query.filter_by(role="employee", is_active=True).order_by(User.name).all()
    return render_template("sites.html", sites=active_sites, employees=employee_options)


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
    )


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
        names = (
            "work_performed",
            "activities_completed",
            "issues_found",
            "recommendations",
            "materials_used",
            "remarks",
            "engineer_name",
            "customer_representative_name",
        )
        values = {name: request.form.get(name, "").strip() for name in names}
        if not all(values.values()):
            flash("Complete every required report field.", "danger")
            return render_template("site_report.html", attendance=attendance, values=values)
        try:
            employee_signature = save_data_image(
                request.form.get("employee_signature", ""),
                current_app.config["UPLOAD_FOLDER"],
            )
            customer_signature = save_data_image(
                request.form.get("customer_signature", ""),
                current_app.config["UPLOAD_FOLDER"],
            )
        except ValueError as error:
            flash(str(error), "danger")
            return render_template("site_report.html", attendance=attendance, values=values)
        attendance.checkout_lat = verification["latitude"]
        attendance.checkout_lon = verification["longitude"]
        attendance.checkout_time = utcnow()
        attendance.status = "completed"
        db.session.add(
            SiteReport(
                attendance=attendance,
                **values,
                employee_signature=employee_signature,
                customer_signature=customer_signature,
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
    return render_template("reports.html", reports=records)


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
    records = Attendance.query.filter_by(status="completed").order_by(Attendance.checkout_time.desc()).all()
    rows = []
    for item in records:
        report = item.report
        rows.append(
            {
                "Employee": item.employee.name,
                "Email": item.employee.email,
                "Customer": item.site.customer_name,
                "Site": item.site.site_name,
                "Address": item.site.address,
                "Check-in": item.checkin_time.isoformat(),
                "Check-out": item.checkout_time.isoformat() if item.checkout_time else "",
                "Check-in latitude": item.checkin_lat,
                "Check-in longitude": item.checkin_lon,
                "Check-out latitude": item.checkout_lat,
                "Check-out longitude": item.checkout_lon,
                "Work performed": report.work_performed if report else "",
                "Activities completed": report.activities_completed if report else "",
                "Issues found": report.issues_found if report else "",
                "Recommendations": report.recommendations if report else "",
                "Materials used": report.materials_used if report else "",
                "Remarks": report.remarks if report else "",
                "Engineer": report.engineer_name if report else "",
                "Customer representative": report.customer_representative_name if report else "",
            }
        )
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                row[key] = "'" + value
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Site Reports")
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="geosite-reports.xlsx",
    )


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
