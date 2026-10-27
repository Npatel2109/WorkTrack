import io
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import ceil

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import REPORT_COMPANY_NAME
from .timeutils import india_date


def activity_workbook(site, visits, leaves, start, end, employee, automatic_leave=()):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SAR-R0"
    ink = "262626"
    charcoal = "3A3838"
    thin = Side(style="thin", color="808080")

    def text(cell, value):
        cell.value = str(value)
        cell.data_type = "s"

    def merged(row, first, last, value):
        sheet.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
        text(sheet.cell(row, first), value)

    sheet.merge_cells("A1:L2")
    text(sheet["A1"], "SITE ACTIVITY REPORT")
    sheet["A1"].font = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
    sheet["A1"].fill = PatternFill("solid", fgColor=ink)
    sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
    merged(3, 1, 12, f"PROJECT : {site.project.name if site.project else site.project_name or 'Not specified'}")
    merged(4, 1, 12, f"CUSTOMER : {site.customer_name}")
    merged(5, 1, 12, f"REPORT UPDATE DATE : {datetime.now(timezone.utc):%d %b %Y}")
    merged(6, 1, 12, f"SITE DETAIL : {site.site_name}")
    merged(7, 1, 12, f"ENGINEER : {employee.name}")
    for column, label in enumerate(("SR NO", "SITE", "DATE", "DAY", "ACTIVITY DETAIL"), 1):
        text(sheet.cell(8, column), label)
    sheet.merge_cells("E8:L8")

    by_date = defaultdict(list)
    for visit in visits:
        by_date[india_date(visit.checkout_time)].append(visit)
    rows = []
    day = start
    while day <= end:
        billable_day_recorded = False
        for visit in by_date[day]:
            report = visit.report
            detail = report.work_performed
            if report.is_billable is None:
                legacy = [
                    report.activities_completed, report.issues_found,
                    report.recommendations, report.materials_used, report.remarks,
                ]
                detail = "\n".join([detail] + [value for value in legacy if value])
                detail += "\n[Billing not recorded in legacy report; counted as 0]"
            elif not report.is_billable:
                detail += "\n[Non-billable]"
            billable_day = report.is_billable is True and not billable_day_recorded
            billable_day_recorded = billable_day_recorded or billable_day
            rows.append((day, int(billable_day), detail))
        leave_rows = [leave for leave in leaves if leave.start_date <= day <= leave.end_date]
        for leave in leave_rows:
            reason = leave.reason
            if leave.period != "full":
                reason = f"{leave.period_label}: {reason}"
            rows.append((day, 0, reason))
        automatic_rows = [item for item in automatic_leave if item["date"] == day]
        for item in automatic_rows:
            rows.append((day, 0, f"Automatic leave ({item['days']:g} day): no site visit or approved work confirmation."))
        if not by_date[day] and not leave_rows and not automatic_rows:
            rows.append((day, 0, "Sunday" if day.weekday() == 6 else "No activity recorded"))
        day += timedelta(days=1)

    for number, (day, days, detail) in enumerate(rows, 1):
        row = number + 8
        sheet.cell(row, 1, number)
        text(sheet.cell(row, 2), site.site_name)
        sheet.cell(row, 3, day).number_format = "dd-mmm-yyyy"
        sheet.cell(row, 4, days)
        merged(row, 5, 12, detail)
        sheet.row_dimensions[row].height = min(240, max(38, ceil(len(detail) / 95) * 15 + detail.count("\n") * 15))
    remarks_row = len(rows) + 9
    merged(remarks_row, 1, 12, "REMARKS")
    remarks = site.report_remarks.splitlines()
    for offset in range(1, 6):
        sheet.cell(remarks_row + offset, 1, offset)
        merged(remarks_row + offset, 3, 12, remarks[offset - 1] if offset <= len(remarks) else "")
        sheet.row_dimensions[remarks_row + offset].height = max(
            20, ceil(len(remarks[offset - 1]) / 105) * 15 if offset <= len(remarks) else 20
        )
    total_row = remarks_row + 6
    merged(total_row, 1, 10, "TOTAL DAYS :")
    sheet.merge_cells(start_row=total_row, start_column=11, end_row=total_row, end_column=12)
    sheet.cell(total_row, 11, sum(days for _, days, _ in rows))
    merged(total_row + 1, 1, 8, f"CUSTOMER : {site.customer_name}")
    merged(total_row + 1, 9, 12, f"M/S {REPORT_COMPANY_NAME}")
    signature_start = total_row + 2
    signature_end = total_row + 6
    for first, last, label in (
        (1, 4, f"Commissioning Manager : {site.commissioning_manager or '____________________'}"),
        (5, 8, f"Project Manager : {site.project_manager or '____________________'}"),
        (9, 12, f"Engineer : {employee.name}"),
    ):
        sheet.merge_cells(
            start_row=signature_start, start_column=first,
            end_row=signature_end, end_column=last,
        )
        name_line = "\nCustomer representative: ____________________" if first == 1 else ""
        text(sheet.cell(signature_start, first), label + name_line + "\n\nSignature: ____________________\nDate: ____________________")
    for row in range(signature_start, signature_end + 1):
        sheet.row_dimensions[row].height = 20

    for row in sheet.iter_rows(min_row=3, max_row=signature_end, max_col=12):
        for cell in row:
            cell.font = Font(name="Calibri", size=11, color=ink)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row_number in (8, remarks_row, total_row + 1):
        for cell in sheet[row_number][:12]:
            cell.fill = PatternFill("solid", fgColor=charcoal)
            cell.font = Font(name="Calibri", size=9, bold=True, color="FFFFFF")
    for row in range(3, 8):
        sheet.row_dimensions[row].height = 30
        for cell in sheet[row][:12]:
            cell.fill = PatternFill("solid", fgColor="F2F2F2")
            cell.font = Font(name="Calibri", size=11, bold=True, color=ink)
    for col, width in enumerate((5.22, 16, 13.55, 5.44, 11.22, 11.78, 8.44, 13.22, 8.78, 13, 9.78, 16.11), 1):
        sheet.column_dimensions[get_column_letter(col)].width = width
    sheet.freeze_panes = "E9"
    sheet.print_title_rows = "1:8"
    sheet.print_area = f"A1:L{signature_end}"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
