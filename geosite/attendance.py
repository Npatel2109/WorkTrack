from datetime import datetime, time, timedelta, timezone

from .extensions import db
from .models import Attendance, AuditLog, utcnow
from .services import haversine_distance


INDIA_TIME = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def daily_cutoff(value):
    local_day = as_utc(value).astimezone(INDIA_TIME).date()
    return datetime.combine(local_day, time(23, 59), tzinfo=INDIA_TIME).astimezone(timezone.utc)


def stop_attendance(attendance, now, coordinates=None, reason=None, automatic=False):
    attendance = Attendance.query.filter_by(id=attendance.id, status="onsite").with_for_update().populate_existing().first()
    if attendance is None:
        return False
    cutoff = daily_cutoff(attendance.checkin_time)
    if as_utc(now) >= cutoff:
        now, coordinates, reason, automatic = cutoff, None, "daily_cutoff", True
    latitude, longitude = coordinates if coordinates else (
        attendance.last_location_lat, attendance.last_location_lon
    )
    captured_at = now if coordinates else attendance.last_location_time
    # Older visits have no tracking fields; their check-in is still a known fix.
    if latitude is None or longitude is None:
        latitude, longitude = attendance.checkin_lat, attendance.checkin_lon
        captured_at = attendance.checkin_location_time or attendance.checkin_time
    source = "current" if coordinates else "last_known"
    if latitude is None or longitude is None:
        latitude, longitude, captured_at, source = None, None, None, "unavailable"
    reference_lat = attendance.site_latitude
    reference_lon = attendance.site_longitude
    if reference_lat is None or reference_lon is None:
        reference_lat, reference_lon = attendance.site.latitude, attendance.site.longitude
    distance = (
        haversine_distance(reference_lat, reference_lon, latitude, longitude)
        if latitude is not None and longitude is not None else None
    )
    values = {
        "checkout_time": now, "checkout_lat": latitude, "checkout_lon": longitude,
        "checkout_distance_m": distance, "checkout_location_time": captured_at,
        "checkout_location_source": source, "checkout_location_reason": reason,
        "auto_closed": automatic, "status": "report_pending",
    }
    if coordinates:
        values.update(last_location_lat=latitude, last_location_lon=longitude, last_location_time=now)
    changed = Attendance.query.filter_by(id=attendance.id, status="onsite").update(values)
    if changed:
        db.session.add(AuditLog(
            user_id=None if automatic else attendance.employee_id,
            action="site_auto_checkout" if automatic else "site_checkout",
            details=f"Attendance {attendance.id}; location={source}; reason={reason}; "
                    f"distance_m={distance}; captured_at={captured_at}",
        ))
    return bool(changed)


def close_overdue_visits(now=None):
    now = as_utc(now or utcnow())
    threshold = daily_cutoff(now)
    if now < threshold:
        threshold -= timedelta(days=1)
    visits = Attendance.query.filter(
        Attendance.status == "onsite", Attendance.checkin_time <= threshold
    ).all()
    count = 0
    for visit in visits:
        cutoff = daily_cutoff(visit.checkin_time)
        count += stop_attendance(visit, cutoff, reason="daily_cutoff", automatic=True)
    if count:
        db.session.commit()
    return count
