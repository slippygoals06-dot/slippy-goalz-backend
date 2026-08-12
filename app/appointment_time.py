"""Parse booking Date/Time strings as Asia/Karachi local time."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

KARACHI = ZoneInfo("Asia/Karachi")

_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]
_TIME_FORMATS = ["%H:%M", "%I:%M %p", "%I:%M%p"]


def parse_appointment_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    """
    Parse common Date + Time string combos into a naive datetime.
    Same format list historically used by bookings email reminders.
    """
    if date_str is None or time_str is None:
        return None
    date_s = str(date_str).strip()
    time_s = str(time_str).strip()
    if not date_s or not time_s:
        return None

    for d_fmt in _DATE_FORMATS:
        for t_fmt in _TIME_FORMATS:
            try:
                return datetime.strptime(f"{date_s} {time_s}", f"{d_fmt} {t_fmt}")
            except ValueError:
                continue
    return None


def parse_appointment_datetime_karachi(
    date_str: str, time_str: str
) -> Optional[datetime]:
    """
    Parse Date + Time as Asia/Karachi (shop local time).
    Returns timezone-aware datetime, or None if unparseable.
    """
    naive = parse_appointment_datetime(date_str, time_str)
    if naive is None:
        return None
    return naive.replace(tzinfo=KARACHI)
