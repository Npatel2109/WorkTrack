import base64
import binascii
import math
import re
import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from pypdf import PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as PdfImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import AuditLog

EARTH_RADIUS_METERS = 6_371_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
DATA_URL_PATTERN = re.compile(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)$")


def haversine_distance(lat1, lon1, lat2, lon2):
    values = tuple(float(value) for value in (lat1, lon1, lat2, lon2))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Coordinates must be finite numbers.")
    lat1, lon1, lat2, lon2 = values
    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90):
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not (-180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        raise ValueError("Longitude must be between -180 and 180 degrees.")
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1, haversine)))


def save_data_image(data_url, destination):
    match = DATA_URL_PATTERN.fullmatch(data_url or "")
    if not match:
        raise ValueError("A captured PNG or JPEG image is required.")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("The image data is invalid.") from error
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("The image exceeds the 5 MB limit.")
    try:
        image = Image.open(BytesIO(raw))
        if image.width * image.height > 20_000_000:
            raise ValueError("The image exceeds the 20 megapixel limit.")
        image.verify()
        image = Image.open(BytesIO(raw)).convert("RGB")
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError) and "20 megapixel" in str(error):
            raise
        raise ValueError("The image could not be decoded.") from error
    image.thumbnail((1600, 1600))
    filename = f"{uuid.uuid4().hex}.png"
    image.save(Path(destination) / filename, format="PNG", optimize=True)
    return filename


def write_audit(user, action, details=None, ip_address=None):
    from .extensions import db

    db.session.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            details=details,
            ip_address=ip_address,
        )
    )


def send_password_reset_email(app, recipient, reset_url):
    if not app.config["MAIL_SERVER"] or not app.config["MAIL_DEFAULT_SENDER"]:
        raise RuntimeError("Password reset email is not configured.")
    message = EmailMessage()
    message["Subject"] = "Reset your GeoSite password"
    message["From"] = app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your GeoSite account.\n\n"
        f"Use this one-time link within one hour:\n{reset_url}\n\n"
        "If you did not request this, ignore this message."
    )
    server = app.config["MAIL_SERVER"]
    port = app.config["MAIL_PORT"]
    with (
        smtplib.SMTP_SSL(server, port, timeout=15)
        if port == 465
        else smtplib.SMTP(server, port, timeout=15)
    ) as smtp:
        if port != 465 and app.config["MAIL_USE_TLS"]:
            smtp.starttls()
        if app.config["MAIL_USERNAME"]:
            smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
        smtp.send_message(message)


def generate_site_report_pdf(attendance, output, upload_folder):
    report = attendance.report
    employee = attendance.employee
    site = attendance.site
    styles = getSampleStyleSheet()
    story = [
        Paragraph("SITE VISIT REPORT", styles["Title"]),
        Spacer(1, 0.12 * inch),
    ]

    def safe(value):
        return escape(str(value or "-")).replace("\n", "<br/>")

    def section(title, rows):
        story.append(Paragraph(title, styles["Heading2"]))
        table = Table(rows, colWidths=[1.7 * inch, 4.9 * inch], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9e0e8")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.extend((table, Spacer(1, 0.15 * inch)))

    section(
        "Engineer Details",
        [["Name", safe(employee.name)], ["Email", safe(employee.email)], ["Mobile", safe(employee.mobile)]],
    )
    section(
        "Customer Details",
        [["Customer", safe(site.customer_name)], ["Site", safe(site.site_name)], ["Address", safe(site.address)]],
    )
    section(
        "Check-In",
        [
            ["Date / Time", attendance.checkin_time.strftime("%Y-%m-%d %H:%M UTC")],
            ["Latitude", str(attendance.checkin_lat)],
            ["Longitude", str(attendance.checkin_lon)],
        ],
    )
    selfie_path = Path(upload_folder) / attendance.checkin_selfie
    if selfie_path.is_file():
        story.extend(
            [
                Paragraph("Check-in Selfie", styles["Heading3"]),
                PdfImage(str(selfie_path), width=1.6 * inch, height=1.6 * inch),
            ]
        )
    section(
        "Check-Out",
        [
            [
                "Date / Time",
                attendance.checkout_time.strftime("%Y-%m-%d %H:%M UTC")
                if attendance.checkout_time
                else "-",
            ],
            ["Latitude", str(attendance.checkout_lat if attendance.checkout_lat is not None else "-")],
            ["Longitude", str(attendance.checkout_lon if attendance.checkout_lon is not None else "-")],
        ],
    )
    if report:
        detail_rows = [
                ["Work Performed", safe(report.work_performed)],
                ["Activities Completed", safe(report.activities_completed)],
                ["Issues Found", safe(report.issues_found)],
                ["Recommendations", safe(report.recommendations)],
                ["Materials Used", safe(report.materials_used)],
                ["Remarks", safe(report.remarks)],
                ["Customer Representative", safe(report.customer_representative_name) if report.customer_representative_name else "____________________________"],
            ]
        if report.is_billable is not None:
            detail_rows = [
                ["Activity Detail", safe(report.work_performed)],
                ["Billing", "Billable (1 day)" if report.is_billable else "Non-billable (0 days)"],
                ["Customer Representative", safe(report.customer_representative_name) if report.customer_representative_name else "____________________________"],
            ]
        section("Site Activity", detail_rows)
        signatures = []
        for label, filename in (
            ("Engineer Signature", report.employee_signature),
            ("Customer Signature", report.customer_signature),
        ):
            image_path = Path(upload_folder) / filename
            if filename and image_path.is_file():
                signatures.append(
                    [label, PdfImage(str(image_path), width=2.2 * inch, height=0.8 * inch)]
                )
            else:
                signatures.append([label, "\n\n____________________________\nDate: _______________________"])
        signature_table = Table(signatures, colWidths=[1.7 * inch, 4.9 * inch])
        signature_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        story.extend([
            Paragraph("Signatures / Offline Approval", styles["Heading2"]),
            signature_table,
        ])
    story.extend(
        [
            Spacer(1, 0.2 * inch),
            Paragraph(
                "Generated by GeoSite Attendance System - "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                styles["Italic"],
            ),
        ]
    )

    def draw_brand(canvas, document):
        canvas.saveState()
        left = document.leftMargin
        top = letter[1] - 0.35 * inch
        canvas.setFillColor(colors.HexColor("#3868ee"))
        canvas.roundRect(left, top - 0.26 * inch, 0.28 * inch, 0.26 * inch, 5, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawCentredString(left + 0.14 * inch, top - 0.19 * inch, "G")
        canvas.setFillColor(colors.HexColor("#17243a"))
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(left + 0.37 * inch, top - 0.17 * inch, "GeoSite")
        canvas.setStrokeColor(colors.HexColor("#d9e0e8"))
        canvas.line(left, 0.48 * inch, letter[0] - document.rightMargin, 0.48 * inch)
        canvas.setFillColor(colors.HexColor("#6d7b90"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(left, 0.32 * inch, "Generated by GeoSite Attendance System")
        canvas.drawRightString(letter[0] - document.rightMargin, 0.32 * inch, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.62 * inch,
        title=f"Site Visit Report - {site.site_name}",
    )
    document.build(story, onFirstPage=draw_brand, onLaterPages=draw_brand)


def merge_site_reports_pdf(attendances, upload_folder):
    writer = PdfWriter()
    for attendance in attendances:
        pdf_data = BytesIO()
        generate_site_report_pdf(attendance, pdf_data, upload_folder)
        pdf_data.seek(0)
        writer.append(pdf_data)
    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output


def generate_invoice_pdf(invoice, output):
    styles = getSampleStyleSheet()
    rows = [
        ["Invoice Number", escape(invoice.invoice_number)],
        ["Invoice Date", invoice.created_at.strftime("%Y-%m-%d")],
        ["Employee", escape(invoice.employee.name)],
        ["Customer", escape(invoice.customer_name)],
        ["Site", escape(invoice.site.site_name)],
        ["Service Dates", f"{invoice.start_date:%Y-%m-%d} to {invoice.end_date:%Y-%m-%d}"],
        ["Description", escape(invoice.description).replace("\n", "<br/>")],
        ["Billable Days", str(invoice.billable_days)],
        ["Rate per Day", f"{invoice.currency} {invoice.daily_rate:,.2f}"],
        ["Total Due", f"{invoice.currency} {invoice.total_amount:,.2f}"],
    ]
    table = Table(rows, colWidths=[1.7 * inch, 4.9 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9e0e8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    document = SimpleDocTemplate(output, pagesize=letter, title=invoice.invoice_number)
    document.build(
        [
            Paragraph("GEOSITE", styles["Title"]),
            Paragraph("INVOICE", styles["Heading1"]),
            Spacer(1, 12),
            table,
            Spacer(1, 20),
            Paragraph("Generated by GeoSite Attendance System", styles["Italic"]),
        ]
    )
