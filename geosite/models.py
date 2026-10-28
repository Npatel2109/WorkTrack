from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager

REPORT_COMPANY_NAME = "Blackpurple automation"


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    mobile = db.Column(db.String(40), nullable=False)
    designation = db.Column(db.String(120), nullable=False, default="Engineer")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="employee")
    created_date = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    auth_version = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0")
    )

    site_assignments = db.relationship("SiteAssignment", back_populates="employee", cascade="all, delete-orphan")
    attendance_records = db.relationship("Attendance", back_populates="employee")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return f"{self.id}:{self.auth_version}"

    @property
    def is_admin(self):
        return self.role == "admin"


class AppSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand_logo_filename = db.Column(db.String(40))
    android_server_url = db.Column(db.String(500), nullable=False, default="")
    android_download_enabled = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )
    public_base_url = db.Column(db.String(500), nullable=False, default="", server_default="")
    mail_server = db.Column(db.String(255), nullable=False, default="", server_default="")
    mail_port = db.Column(db.Integer, nullable=False, default=465, server_default="465")
    mail_security = db.Column(db.String(10), nullable=False, default="ssl", server_default="ssl")
    mail_username = db.Column(db.String(255), nullable=False, default="", server_default="")
    mail_password = db.Column(db.String(500), nullable=False, default="", server_default="")
    mail_sender = db.Column(db.String(255), nullable=False, default="", server_default="")
    referral_notification_email = db.Column(db.String(255), nullable=False, default="", server_default="")
    po_notification_email = db.Column(db.String(255), nullable=False, default="", server_default="")


class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    period = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(2000), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime(timezone=True))

    employee = db.relationship("User", foreign_keys=[employee_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

    __table_args__ = (
        db.CheckConstraint("end_date >= start_date", name="leave_date_order"),
        db.CheckConstraint(
            "period IN ('full', 'morning', 'afternoon')", name="leave_valid_period"
        ),
        db.CheckConstraint(
            "period = 'full' OR start_date = end_date", name="leave_half_day_date"
        ),
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="leave_valid_status"
        ),
    )

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1 if self.period == "full" else 0.5

    @property
    def period_label(self):
        return {"full": "Full day", "morning": "Half day - morning", "afternoon": "Half day - afternoon"}[self.period]


class AssignedSite(db.Model):
    __tablename__ = "assigned_sites"

    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(160), nullable=False)
    customer_name = db.Column(db.String(160), nullable=False)
    project_name = db.Column(db.String(250), nullable=False, default="", server_default="")
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), index=True)
    report_company = db.Column(db.String(160), nullable=False, default="", server_default="")
    commissioning_manager = db.Column(db.String(120), nullable=False, default="", server_default="")
    project_manager = db.Column(db.String(120), nullable=False, default="", server_default="")
    report_remarks = db.Column(db.Text, nullable=False, default="", server_default="")
    address = db.Column(db.Text, nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    allowed_radius = db.Column(db.Float, nullable=False, default=100)
    assigned_date = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    assignments = db.relationship("SiteAssignment", back_populates="site", cascade="all, delete-orphan")
    attendance_records = db.relationship("Attendance", back_populates="site")
    project = db.relationship("Project", back_populates="sites")


class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_number = db.Column(db.String(80), nullable=False, unique=True)
    name = db.Column(db.String(250), nullable=False, unique=True)
    sites = db.relationship("AssignedSite", back_populates="project")


class LeaveAllowanceAccount(db.Model):
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    joining_date = db.Column(db.Date, nullable=False)
    opening_days = db.Column(db.Numeric(8, 1), nullable=False, default=0)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        db.CheckConstraint("opening_days >= 0", name="leave_opening_nonnegative"),
    )


class MonthlyLeaveEntitlement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    month = db.Column(db.Date, nullable=False)
    days = db.Column(db.Numeric(6, 1), nullable=False)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    employee = db.relationship("User", foreign_keys=[employee_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    __table_args__ = (
        db.UniqueConstraint("employee_id", "month", name="uq_leave_entitlement_employee_month"),
        db.CheckConstraint("days >= 0 AND days <= 31", name="leave_entitlement_valid_days"),
    )


class AttendancePolicy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    effective_from = db.Column(db.Date, nullable=False)


class DailyWorkStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    work_date = db.Column(db.Date, nullable=False, index=True)
    work_type = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(2000), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime(timezone=True))

    employee = db.relationship("User", foreign_keys=[employee_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    __table_args__ = (
        db.UniqueConstraint("employee_id", "work_date", name="uq_daily_work_employee_date"),
        db.CheckConstraint("work_type IN ('wfh', 'travelling', 'training')", name="daily_work_valid_type"),
        db.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="daily_work_valid_status"),
    )

    @property
    def type_label(self):
        return {"wfh": "WFH", "travelling": "Travelling", "training": "Training"}[self.work_type]


class SiteAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    site_id = db.Column(db.Integer, db.ForeignKey("assigned_sites.id"), nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    assigned_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    employee = db.relationship("User", back_populates="site_assignments")
    site = db.relationship("AssignedSite", back_populates="assignments")
    __table_args__ = (db.UniqueConstraint("employee_id", "site_id", name="uq_employee_site_assignment"),)


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey("assigned_sites.id"), nullable=False)
    checkin_time = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    checkout_time = db.Column(db.DateTime(timezone=True))
    checkin_lat = db.Column(db.Float, nullable=False)
    checkin_lon = db.Column(db.Float, nullable=False)
    checkout_lat = db.Column(db.Float)
    checkout_lon = db.Column(db.Float)
    checkin_selfie = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="onsite")
    site_latitude = db.Column(db.Float)
    site_longitude = db.Column(db.Float)
    site_radius_m = db.Column(db.Float)
    checkin_distance_m = db.Column(db.Float)
    checkout_distance_m = db.Column(db.Float)
    checkin_location_time = db.Column(db.DateTime(timezone=True))
    last_location_lat = db.Column(db.Float)
    last_location_lon = db.Column(db.Float)
    last_location_time = db.Column(db.DateTime(timezone=True))
    checkout_location_time = db.Column(db.DateTime(timezone=True))
    checkout_location_source = db.Column(db.String(20))
    checkout_location_reason = db.Column(db.String(40))
    auto_closed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    employee = db.relationship("User", back_populates="attendance_records")
    site = db.relationship("AssignedSite", back_populates="attendance_records")
    report = db.relationship(
        "SiteReport",
        back_populates="attendance",
        uselist=False,
        cascade="all, delete-orphan",
    )


class SiteReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attendance_id = db.Column(
        db.Integer, db.ForeignKey("attendance.id"), nullable=False, unique=True
    )
    work_performed = db.Column(db.Text, nullable=False)
    is_billable = db.Column(db.Boolean)
    activities_completed = db.Column(db.Text, nullable=False)
    issues_found = db.Column(db.Text, nullable=False)
    recommendations = db.Column(db.Text, nullable=False)
    materials_used = db.Column(db.Text, nullable=False)
    remarks = db.Column(db.Text, nullable=False)
    engineer_name = db.Column(db.String(120), nullable=False)
    customer_representative_name = db.Column(db.String(120), nullable=False)
    employee_signature = db.Column(db.String(255), nullable=False)
    customer_signature = db.Column(db.String(255), nullable=False)
    report_date = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    attendance = db.relationship("Attendance", back_populates="report")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User")


class SubmittedReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    site_id = db.Column(db.Integer, db.ForeignKey("assigned_sites.id"), index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    revision_number = db.Column(db.Integer, nullable=False, default=1)

    employee = db.relationship("User")
    site = db.relationship("AssignedSite")
    versions = db.relationship(
        "SubmittedReportVersion", back_populates="report",
        order_by="SubmittedReportVersion.revision_number.desc()",
        cascade="all, delete-orphan",
    )

    __mapper_args__ = {"version_id_col": revision_number}

    @property
    def current_version(self):
        return self.versions[0]


class SubmittedReportVersion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("submitted_report.id"), nullable=False, index=True)
    revision_number = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    total_mandays = db.Column(db.Integer, nullable=False)
    filename = db.Column(db.String(40), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    pdf_replaced = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    report = db.relationship("SubmittedReport", back_populates="versions")

    __table_args__ = (
        db.UniqueConstraint("report_id", "revision_number", name="uq_submitted_report_revision"),
        db.CheckConstraint("revision_number >= 1", name="submitted_report_valid_revision"),
        db.CheckConstraint("end_date >= start_date", name="submitted_report_date_order"),
        db.CheckConstraint("total_mandays >= 1", name="submitted_report_positive_mandays"),
    )


class CandidateReferral(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    candidate_name = db.Column(db.String(120), nullable=False)
    candidate_email = db.Column(db.String(255), nullable=False)
    candidate_mobile = db.Column(db.String(40), nullable=False, default="", server_default="")
    resume_filename = db.Column(db.String(40), nullable=False, default="", server_default="")
    resume_original_filename = db.Column(db.String(255), nullable=False, default="", server_default="")
    notes = db.Column(db.Text, nullable=False, default="", server_default="")
    status = db.Column(db.String(20), nullable=False, default="submitted", server_default="submitted")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    employee = db.relationship("User")


class PurchaseOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(120), nullable=False, unique=True, index=True)
    customer_name = db.Column(db.String(160), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    site_id = db.Column(db.Integer, db.ForeignKey("assigned_sites.id"), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    total_mandays = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    used_mandays = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    invoice_generated = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    money_received = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    completion_email_sent = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    employee = db.relationship("User")
    site = db.relationship("AssignedSite")


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(40), unique=True, nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey("assigned_sites.id"), nullable=False)
    customer_name = db.Column(db.String(160), nullable=False)
    po_number = db.Column(db.String(120), nullable=False, default="", server_default="")
    po_date = db.Column(db.Date)
    description = db.Column(db.Text, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    billable_days = db.Column(db.Integer, nullable=False)
    daily_rate = db.Column(db.Numeric(12, 2), nullable=False)
    total_amount = db.Column(db.Numeric(14, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="INR")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    employee = db.relationship("User", foreign_keys=[employee_id])
    site = db.relationship("AssignedSite")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


@login_manager.user_loader
def load_user(user_id):
    try:
        raw_id, separator, raw_version = user_id.partition(":")
        user = db.session.get(User, int(raw_id))
        if user and (
            (separator and int(raw_version) != user.auth_version)
            or (not separator and user.auth_version != 0)
        ):
            return None
        return user
    except (TypeError, ValueError):
        return None
