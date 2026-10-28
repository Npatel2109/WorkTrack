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

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as PdfImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.utils import secure_filename

from .extensions import db
from .models import AuditLog
from .timeutils import format_ist

EARTH_RADIUS_METERS = 6_371_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BRAND_LOGO_BYTES = 2 * 1024 * 1024
MAX_REPORT_PDF_BYTES = 5 * 1024 * 1024
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


def save_brand_logo(upload, destination):
    if upload is None:
        raise ValueError("Choose a logo image to upload.")
    raw = upload.stream.read(MAX_BRAND_LOGO_BYTES + 1)
    if not raw:
        raise ValueError("Choose a logo image to upload.")
    if len(raw) > MAX_BRAND_LOGO_BYTES:
        raise ValueError("The logo image exceeds the 2 MB limit.")

    try:
        image = Image.open(BytesIO(raw))
        output_format = image.format
        if output_format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("Upload a PNG, JPEG, or WebP logo image.")
        if image.width * image.height > 20_000_000:
            raise ValueError("The logo image exceeds the 20 megapixel limit.")
        image.load()
        image = ImageOps.exif_transpose(image)
        has_transparency = "A" in image.getbands() or "transparency" in image.info
        image.thumbnail((1024, 1024))
        if output_format == "JPEG":
            image = image.convert("RGB")
            extension = "jpg"
        else:
            image = image.convert("RGBA" if has_transparency else "RGB")
            extension = output_format.lower()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("The logo image could not be decoded.") from error

    filename = f"{uuid.uuid4().hex}.{extension}"
    Path(destination).mkdir(parents=True, exist_ok=True)
    path = Path(destination) / filename
    try:
        with path.open("xb") as output:
            image.save(output, format=output_format, optimize=True)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return filename


def save_report_pdf(upload, destination):
    original_filename = secure_filename(upload.filename or "")
    if not original_filename.lower().endswith(".pdf") or len(original_filename) > 255:
        raise ValueError("Choose a PDF file with a filename of at most 255 characters.")
    raw = upload.stream.read(MAX_REPORT_PDF_BYTES + 1)
    if not raw:
        raise ValueError("The PDF file is empty.")
    if len(raw) > MAX_REPORT_PDF_BYTES:
        raise ValueError("The PDF file exceeds the 5 MB limit.")
    if not raw.startswith(b"%PDF-"):
        raise ValueError("Upload a valid PDF report, not a renamed file.")
    try:
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise ValueError("Password-protected PDF reports are not supported.")
        if not len(reader.pages):
            raise ValueError("The PDF report must contain at least one page.")
    except PyPdfError as error:
        raise ValueError("The PDF could not be read. Upload a valid, unencrypted PDF.") from error
    filename = f"{uuid.uuid4().hex}.pdf"
    path = Path(destination) / filename
    output = path.open("xb")
    try:
        with output:
            output.write(raw)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return filename, original_filename


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


def mail_settings(app):
    """Admin-saved settings win; otherwise fall back to MAIL_* environment config."""
    from .models import AppSettings

    saved = db.session.get(AppSettings, 1)
    if saved and saved.mail_server:
        return {
            "server": saved.mail_server, "port": saved.mail_port,
            "security": saved.mail_security, "username": saved.mail_username,
            "password": saved.mail_password, "sender": saved.mail_sender,
        }
    port = app.config["MAIL_PORT"]
    return {
        "server": app.config["MAIL_SERVER"], "port": port,
        "security": "ssl" if port == 465 else "starttls" if app.config["MAIL_USE_TLS"] else "none",
        "username": app.config["MAIL_USERNAME"], "password": app.config["MAIL_PASSWORD"],
        "sender": app.config["MAIL_DEFAULT_SENDER"],
    }


def public_base_url(app):
    from .models import AppSettings

    saved = db.session.get(AppSettings, 1)
    return ((saved.public_base_url if saved else "") or app.config["PUBLIC_BASE_URL"]).rstrip("/")


def send_password_reset_email(app, recipient, reset_url):
    send_email(
        app, recipient, "Reset your GeoSite password",
        "A password reset was requested for your GeoSite account.\n\n"
        f"Use this one-time link within one hour:\n{reset_url}\n\n"
        "If you did not request this, ignore this message.",
    )


def send_email(app, recipient, subject, body, attachments=None):
    config = mail_settings(app)
    if not config["server"] or not config["sender"]:
        raise RuntimeError("Password reset email is not configured.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["sender"]
    message["To"] = recipient
    message.set_content(body)
    for attachment in attachments or []:
        filename, path = attachment
        data = Path(path).read_bytes()
        maintype, subtype = ("application", "octet-stream")
        import mimetypes
        guessed = mimetypes.guess_type(filename)[0]
        if guessed:
            maintype, subtype = guessed.split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    with (
        smtplib.SMTP_SSL(config["server"], config["port"], timeout=15)
        if config["security"] == "ssl"
        else smtplib.SMTP(config["server"], config["port"], timeout=15)
    ) as smtp:
        if config["security"] == "starttls":
            smtp.starttls()
        if config["username"]:
            smtp.login(config["username"], config["password"])
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
            ["Date / Time", format_ist(attendance.checkin_time)],
            ["Latitude", str(attendance.checkin_lat)],
            ["Longitude", str(attendance.checkin_lon)],
            ["Distance from assigned site", f"{attendance.checkin_distance_m / 1000:.3f} km" if attendance.checkin_distance_m is not None else "Not recorded"],
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
                format_ist(attendance.checkout_time),
            ],
            ["Latitude", str(attendance.checkout_lat if attendance.checkout_lat is not None else "-")],
            ["Longitude", str(attendance.checkout_lon if attendance.checkout_lon is not None else "-")],
            ["Distance from assigned site", f"{attendance.checkout_distance_m / 1000:.3f} km" if attendance.checkout_distance_m is not None else "Not recorded"],
            ["Location source", safe(attendance.checkout_location_source or "Not recorded")],
            ["Location captured at", format_ist(attendance.checkout_location_time, "%d %b %Y %H:%M:%S IST") if attendance.checkout_location_time else "Not recorded"],
            ["Stop method", "Automatic (23:59 India)" if attendance.auto_closed else "Manual"],
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
                + format_ist(datetime.now(timezone.utc)),
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
        ["PO Number", escape(invoice.po_number) if invoice.po_number else "Not specified"],
        ["PO Date", invoice.po_date.strftime("%Y-%m-%d") if invoice.po_date else "Not specified"],
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
