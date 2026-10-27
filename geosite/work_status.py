from calendar import monthrange
from datetime import datetime, time, timedelta, timezone

from .attendance import daily_cutoff
from .extensions import db
from .models import Attendance, AttendancePolicy, DailyWorkStatus, LeaveAllowanceAccount, LeaveRequest, MonthlyLeaveEntitlement, utcnow
from .timeutils import INDIA_TIME, as_utc, india_date


def month_bounds(today):
    return today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])


def approved_leave_today(employee_id, today):
    leaves = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee_id, LeaveRequest.status == "approved",
        LeaveRequest.start_date <= today, LeaveRequest.end_date >= today,
    ).all()
    full_day = any(item.period == "full" for item in leaves) or {
        item.period for item in leaves
    } >= {"morning", "afternoon"}
    return leaves, full_day


def leave_overlap(employee_id, start, end, period, exclude_id=None, statuses=("pending", "approved")):
    query = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee_id, LeaveRequest.status.in_(statuses),
        LeaveRequest.start_date <= end, LeaveRequest.end_date >= start,
    )
    if period != "full":
        query = query.filter(LeaveRequest.period.in_(("full", period)))
    if exclude_id is not None:
        query = query.filter(LeaveRequest.id != exclude_id)
    return query.first() is not None


def monthly_leave_balance(employee, month, now=None):
    now = as_utc(now or utcnow())
    today = india_date(now)
    first, last = month_bounds(month)
    through = min(today, last)
    account = db.session.get(LeaveAllowanceAccount, employee.id)
    joining = account.joining_date if account else india_date(employee.created_date)
    opening = float(account.opening_days) if account else 0
    rates = MonthlyLeaveEntitlement.query.filter_by(employee_id=employee.id).order_by(
        MonthlyLeaveEntitlement.month
    ).all()
    rate = 2
    selected_rate = 2
    for item in rates:
        if item.month <= first:
            selected_rate = float(item.days)
    summary = attendance_summary(employee, joining, through, now=now) if joining <= through else None
    history = []
    credited = opening if joining <= through else 0
    used = 0
    cursor = joining.replace(day=1)
    while joining <= through and cursor <= through:
        for item in rates:
            if item.month <= cursor:
                rate = float(item.days)
        _, month_end = month_bounds(cursor)
        start, end = max(joining, cursor), min(through, month_end)
        approved = 0
        if summary:
            for leave in summary["leaves"]:
                lower, upper = max(start, leave.start_date), min(end, leave.end_date)
                if leave.status == "approved" and lower <= upper:
                    approved += (upper - lower).days + 1 if leave.period == "full" else 0.5
        automatic = sum(
            item["days"] for item in summary["automatic"] if start <= item["date"] <= end
        ) if summary else 0
        credited += rate
        used += approved + automatic
        history.append({
            "month": cursor, "credit": rate, "approved": approved, "automatic": automatic,
            "credited": credited, "used": used, "remaining": credited - used,
        })
        cursor = month_end + timedelta(days=1)
    # Approved future dates are commitments, not leave already taken.
    future = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee.id, LeaveRequest.status == "approved",
        LeaveRequest.end_date > through, LeaveRequest.end_date >= joining,
    ).all()
    reserved = 0
    for leave in future:
        start = max(leave.start_date, joining, through + timedelta(days=1))
        if start <= leave.end_date:
            reserved += (leave.end_date - start).days + 1 if leave.period == "full" else 0.5
    return {
        "entitlement": credited, "monthly_rate": selected_rate, "used": used,
        "remaining": credited - used, "reserved": reserved, "available": credited - used - reserved,
        "joining_date": joining, "opening_days": opening, "through": through,
        "history": history, "rate_changes": rates,
    }


def attendance_summary(employee, start, end, now=None):
    now = as_utc(now or utcnow())
    policy = db.session.get(AttendancePolicy, 1)
    leaves = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee.id,
        LeaveRequest.start_date <= end, LeaveRequest.end_date >= start,
    ).order_by(LeaveRequest.start_date, LeaveRequest.id).all()
    totals = dict(approved=0, pending=0, rejected=0, automatic=0)
    approved_by_day = {}
    for leave in leaves:
        first, last = max(start, leave.start_date), min(end, leave.end_date)
        amount = 1 if leave.period == "full" else 0.5
        totals[leave.status] += ((last - first).days + 1) * amount
        if leave.status == "approved":
            day = first
            while day <= last:
                approved_by_day[day] = min(1, approved_by_day.get(day, 0) + amount)
                day += timedelta(days=1)
    confirmations = DailyWorkStatus.query.filter(
        DailyWorkStatus.employee_id == employee.id,
        DailyWorkStatus.work_date >= start, DailyWorkStatus.work_date <= end,
    ).order_by(DailyWorkStatus.work_date.desc()).all()
    approved_work = {item.work_date for item in confirmations if item.status == "approved"}
    lower = datetime.combine(start, time.min, tzinfo=INDIA_TIME).astimezone(timezone.utc)
    upper = datetime.combine(end + timedelta(days=1), time.min, tzinfo=INDIA_TIME).astimezone(timezone.utc)
    onsite_days = {
        india_date(visit.checkin_time) for visit in Attendance.query.filter(
            Attendance.employee_id == employee.id,
            Attendance.checkin_time >= lower, Attendance.checkin_time < upper,
        ).all()
    }
    automatic = []
    if policy:
        day = max(start, policy.effective_from, india_date(employee.created_date))
        last = min(end, india_date(now))
        while day <= last:
            cutoff = daily_cutoff(datetime.combine(day, time.min, tzinfo=INDIA_TIME))
            if day.weekday() < 6 and now >= cutoff and day not in onsite_days and day not in approved_work:
                days = 1 - approved_by_day.get(day, 0)
                if days:
                    automatic.append({"date": day, "days": days})
                    totals["automatic"] += days
            day += timedelta(days=1)
    return {
        "leaves": leaves, "totals": totals, "automatic": automatic,
        "confirmations": confirmations,
        "total_leave": totals["approved"] + totals["automatic"],
        "policy_start": policy.effective_from if policy else None,
    }
