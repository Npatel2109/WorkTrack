from datetime import datetime, timedelta, timezone


INDIA_TIME = timezone(timedelta(hours=5, minutes=30), name="IST")


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def india_date(value):
    return as_utc(value).astimezone(INDIA_TIME).date()


def format_ist(value, pattern="%d %b %Y %H:%M IST"):
    return as_utc(value).astimezone(INDIA_TIME).strftime(pattern) if value else "-"
