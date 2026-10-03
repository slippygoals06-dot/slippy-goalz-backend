"""Build deterministic snapshots from the Slippy Goalz Supabase schema."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from supabase import create_client

from app.appointment_time import parse_appointment_datetime
from app.config import SUPABASE_KEY, SUPABASE_URL

KARACHI = ZoneInfo("Asia/Karachi")
_METRICS = ("revenue", "bookings", "completed", "cancelled", "no_shows")
_NO_SHOW_STATUSES = {"no-show", "no show", "noshow", "no_show"}
_BOOKING_FIELDS = (
    '"Date","Time","Status",amount,"Booking ID","Name","Phone","Service",'
    'customer_id,deposit_amount,deposit_paid,"Payment Status"'
)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def _local_as_of(as_of: Optional[date | datetime]) -> datetime:
    if as_of is None:
        return datetime.now(KARACHI)
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            return as_of.replace(tzinfo=KARACHI)
        return as_of.astimezone(KARACHI)
    if isinstance(as_of, date):
        return datetime.combine(as_of, time.min).replace(tzinfo=KARACHI)
    raise TypeError("as_of must be a date, datetime, or None")


def _parse_booking_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _appointment_at(row: Mapping[str, Any]) -> Optional[datetime]:
    naive = parse_appointment_datetime(
        str(row.get("Date") or ""),
        str(row.get("Time") or ""),
    )
    return naive.replace(tzinfo=KARACHI) if naive else None


def _amount(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _fetch_bookings(start: date, end: date) -> list[dict[str, Any]]:
    """Fetch the bounded booking window using PostgREST's typed query builder."""
    result = (
        supabase.table("bookings")
        .select(_BOOKING_FIELDS)
        .gte('"Date"', start.isoformat())
        .lte('"Date"', end.isoformat())
        .execute()
    )
    return result.data or []


def _fetch_slots(start: date, end: date) -> list[dict[str, Any]]:
    """Fetch configured slots for the baseline and next-48-hour window."""
    result = (
        supabase.table("slots")
        .select('id,"Date","Time","Status"')
        .gte('"Date"', start.isoformat())
        .lte('"Date"', end.isoformat())
        .execute()
    )
    return result.data or []


def _fetch_booking_events(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Fetch recent booking funnel events for abandonment detection."""
    result = (
        supabase.table("booking_events")
        .select("id,booking_ref,customer_ref,event_type,created_at")
        .gte("created_at", start.isoformat())
        .lte("created_at", end.isoformat())
        .order("created_at")
        .execute()
    )
    return result.data or []


def _daily_metrics(rows: Sequence[Mapping[str, Any]], day: date) -> dict[str, Any]:
    day_rows = [
        row for row in rows if _parse_booking_date(row.get("Date")) == day
    ]
    completed = 0
    cancelled = 0
    no_shows = 0
    revenue = Decimal("0")
    has_unknown_completed_amount = False

    for row in day_rows:
        status = str(row.get("Status") or "").strip().lower()
        if status == "completed":
            completed += 1
            amount = _amount(row.get("amount"))
            if amount is None:
                has_unknown_completed_amount = True
            else:
                revenue += amount
        elif status == "cancelled":
            cancelled += 1
        elif status in _NO_SHOW_STATUSES:
            no_shows += 1

    return {
        "revenue": None if has_unknown_completed_amount else revenue,
        "bookings": len(day_rows),
        "completed": completed,
        "cancelled": cancelled,
        "no_shows": no_shows,
    }


def _completed_amounts(
    bookings: Sequence[Mapping[str, Any]],
    days: set[date],
) -> list[Decimal]:
    amounts = []
    for row in bookings:
        if (
            _parse_booking_date(row.get("Date")) in days
            and str(row.get("Status") or "").strip().lower() == "completed"
        ):
            amount = _amount(row.get("amount"))
            if amount is not None and amount >= 0:
                amounts.append(amount)
    return amounts


def _average_amount(amounts: Sequence[Decimal]) -> Optional[Decimal]:
    if not amounts:
        return None
    return (sum(amounts, Decimal("0")) / len(amounts)).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )


def _estimate_slot_value(
    bookings: Sequence[Mapping[str, Any]],
    slot_day: date,
    today: date,
) -> tuple[Optional[Decimal], Optional[str]]:
    """Estimate a slot from prior completed booking amounts, never today's data."""
    history_start = today - timedelta(days=28)
    matching_days = {
        slot_day - timedelta(days=7 * week)
        for week in range(1, 5)
        if history_start <= slot_day - timedelta(days=7 * week) < today
    }
    weekday_amounts = _completed_amounts(bookings, matching_days)
    if weekday_amounts:
        return _average_amount(weekday_amounts), "same_weekday_4_week_average"

    overall_days = {
        today - timedelta(days=offset)
        for offset in range(1, 29)
    }
    overall_amounts = _completed_amounts(bookings, overall_days)
    if overall_amounts:
        return _average_amount(overall_amounts), "overall_4_week_average"
    return None, None


def _unpaid_deposits(
    bookings: Sequence[Mapping[str, Any]],
    as_of: datetime,
) -> list[dict[str, Any]]:
    """List upcoming active bookings with a required but unconfirmed deposit."""
    horizon = as_of + timedelta(hours=48)
    excluded_statuses = {
        "cancelled",
        "canceled",
        "rejected",
        "completed",
        *_NO_SHOW_STATUSES,
    }
    unpaid = []
    for row in bookings:
        appointment = _appointment_at(row)
        if appointment is None or not as_of <= appointment <= horizon:
            continue
        if str(row.get("Status") or "").strip().lower() in excluded_statuses:
            continue
        deposit_amount = _amount(row.get("deposit_amount"))
        if deposit_amount is None or deposit_amount <= 0:
            continue
        if row.get("deposit_paid") is True:
            continue
        unpaid.append(
            {
                "booking_ref": row.get("Booking ID"),
                "customer_ref": row.get("customer_id") or row.get("Phone"),
                "date": appointment.date().isoformat(),
                "time": appointment.strftime("%H:%M"),
                "deposit_amount": deposit_amount,
                "deposit_paid": False,
                "payment_status": row.get("Payment Status"),
            }
        )
    unpaid.sort(key=lambda row: (row["date"], row["time"]))
    return unpaid


def _percent_difference(actual: Any, baseline: Any) -> Optional[Decimal]:
    if actual is None or baseline is None:
        return None
    normal = Decimal(str(baseline))
    value = Decimal(str(actual))
    if normal == 0:
        return Decimal("0") if value == 0 else None
    return (((value - normal) / abs(normal)) * 100).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _delta(today: Any, yesterday: Any) -> Optional[Any]:
    if today is None or yesterday is None:
        return None
    return today - yesterday


def _snapshot_from_rows(
    business_id: str,
    as_of: datetime,
    bookings: Sequence[Mapping[str, Any]],
    slots: Sequence[Mapping[str, Any]],
    booking_events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    today_date = as_of.date()
    today = _daily_metrics(bookings, today_date)
    yesterday = _daily_metrics(bookings, today_date - timedelta(days=1))
    slot_dates = {
        day
        for row in slots
        if (day := _parse_booking_date(row.get("Date"))) is not None
    }
    booking_dates = {
        day
        for row in bookings
        if (day := _parse_booking_date(row.get("Date"))) is not None
    }

    week_days = [today_date - timedelta(days=7 * week) for week in range(1, 5)]
    observed_days = [
        day for day in week_days if day in slot_dates or day in booking_dates
    ]
    weekly_metrics = [_daily_metrics(bookings, day) for day in observed_days]
    baseline: dict[str, Any] = {}
    for metric in _METRICS:
        values = [
            row[metric]
            for row in weekly_metrics
            if row[metric] is not None
        ]
        baseline[metric] = (
            sum(values, Decimal("0")) / len(values)
            if values
            else None
        )

    revenue_sample_size = sum(
        row["revenue"] is not None for row in weekly_metrics
    )
    baseline["sample_size"] = len(observed_days)
    baseline["revenue_sample_size"] = revenue_sample_size

    vs_normal = {
        metric: _percent_difference(
            today[metric],
            baseline[metric],
        )
        if (
            revenue_sample_size >= 2
            if metric == "revenue"
            else len(observed_days) >= 2
        )
        else None
        for metric in _METRICS
    }

    horizon = as_of + timedelta(hours=48)
    overall_history_days = {
        today_date - timedelta(days=offset)
        for offset in range(1, 29)
    }
    overall_average_amount = _average_amount(
        _completed_amounts(bookings, overall_history_days)
    )
    empty_slots = []
    for row in slots:
        if str(row.get("Status") or "").strip().lower() != "available":
            continue
        appointment = _appointment_at(row)
        if appointment is None or not as_of <= appointment <= horizon:
            continue
        potential_rs, value_basis = _estimate_slot_value(
            bookings,
            appointment.date(),
            today_date,
        )
        empty_slots.append(
            {
                "date": appointment.date().isoformat(),
                "time": appointment.strftime("%H:%M"),
                "potential_rs": potential_rs,
                "estimated": potential_rs is not None,
                "value_basis": value_basis,
            }
        )
    empty_slots.sort(key=lambda row: (row["date"], row["time"]))
    unpaid_deposits = _unpaid_deposits(bookings, as_of)

    return {
        "business_id": business_id,
        "as_of": as_of.isoformat(),
        "today": today,
        "baseline": baseline,
        "vs_normal": vs_normal,
        "funnel": {
            "chat_started": None,
            "booking_started": None,
            "paid": None,
        },
        "booking_events": list(booking_events),
        "abandoned_bookings": [],
        "estimated_average_booking_value": overall_average_amount,
        "unpaid_deposits": {
            "count": len(unpaid_deposits),
            "items": unpaid_deposits,
        },
        "overdue_jobs": {
            "count": None,
            "items": [],
        },
        "next_48h_empty_slots": {
            "count": len(empty_slots),
            "slots": empty_slots,
        },
        "change_map": {
            metric: _delta(today[metric], yesterday[metric])
            for metric in _METRICS
        },
        "tracking": {
            "revenue_basis": "completed booking amounts by appointment date",
            "revenue_collected": False,
            "empty_slots": True,
            "funnel": False,
            "abandoned_bookings": True,
            "unpaid_deposits": True,
            "overdue_jobs": False,
            "customers": False,
            "empty_slot_potential_value": any(
                slot["estimated"] for slot in empty_slots
            ),
        },
    }


def build_snapshot(
    business_id: str,
    as_of: Optional[date | datetime] = None,
) -> dict[str, Any]:
    """Build a deterministic snapshot using Asia/Karachi business dates."""
    local_as_of = _local_as_of(as_of)
    today = local_as_of.date()
    baseline_start = today - timedelta(days=28)
    slot_end = (local_as_of + timedelta(hours=48)).date()
    bookings = _fetch_bookings(baseline_start, max(today, slot_end))
    slots = _fetch_slots(baseline_start, slot_end)
    booking_events = _fetch_booking_events(
        local_as_of - timedelta(days=30),
        local_as_of,
    )
    return _snapshot_from_rows(
        business_id=business_id,
        as_of=local_as_of,
        bookings=bookings,
        slots=slots,
        booking_events=booking_events,
    )
