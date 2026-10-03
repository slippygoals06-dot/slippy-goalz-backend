"""Detect booking attempts without a customer confirmation within two hours."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

from app.manager.models import Finding


def _event_time(event: Mapping[str, Any]) -> Optional[datetime]:
    value = event.get("created_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _estimated_value(snapshot: Mapping[str, Any]) -> Optional[Decimal]:
    value = snapshot.get("estimated_average_booking_value")
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount >= 0 else None


def confirmed_booking_start_ids(
    snapshot: Mapping[str, Any],
) -> dict[str, str]:
    """Match each submission to one preceding start for that customer."""
    events = list(snapshot.get("booking_events") or [])
    starts_by_customer: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = {}
    confirmations = []
    for event in events:
        event_time = _event_time(event)
        customer_ref = str(event.get("customer_ref") or "").strip()
        if not event_time or not customer_ref:
            continue
        if event.get("event_type") == "booking_started":
            if event.get("id"):
                starts_by_customer.setdefault(customer_ref, []).append(
                    (event_time, event)
                )
        elif event.get("event_type") == "booking_confirmed":
            confirmations.append((event_time, customer_ref, event))

    for starts in starts_by_customer.values():
        starts.sort(key=lambda item: item[0])
    confirmations.sort(key=lambda item: item[0])
    resolved: dict[str, str] = {}
    for confirmed_at, customer_ref, confirmed in confirmations:
        unmatched = [
            (started_at, started)
            for started_at, started in starts_by_customer.get(customer_ref, [])
            if str(started.get("id")) not in resolved
            and started_at <= confirmed_at
        ]
        if not unmatched:
            continue
        confirmed_booking_ref = confirmed.get("booking_ref")
        exact_booking = [
            item for item in unmatched
            if confirmed_booking_ref
            and item[1].get("booking_ref") == confirmed_booking_ref
        ]
        _started_at, started = (exact_booking or unmatched)[-1]
        resolved[str(started["id"])] = confirmed_at.isoformat()
    return resolved


def detect_abandoned_bookings(
    snapshot: Mapping[str, Any],
) -> list[Finding]:
    """Flag started attempts that aged two hours without a matching submission."""
    try:
        as_of = datetime.fromisoformat(
            str(snapshot.get("as_of") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return []
    if as_of.tzinfo is None:
        return []

    events = list(snapshot.get("booking_events") or [])
    resolved = confirmed_booking_start_ids(snapshot)
    estimated_value = _estimated_value(snapshot)
    findings = []
    for started in events:
        if started.get("event_type") != "booking_started":
            continue
        started_at = _event_time(started)
        customer_ref = str(started.get("customer_ref") or "").strip()
        event_id = str(started.get("id") or "").strip()
        if not started_at or not customer_ref or not event_id:
            continue
        deadline = started_at + timedelta(hours=2)
        if as_of < deadline:
            continue
        if event_id in resolved:
            continue

        age_minutes = int((as_of - started_at).total_seconds() // 60)
        amount = estimated_value or Decimal("0")
        findings.append(
            Finding(
                type="abandoned_bookings",
                severity="yellow",
                title="Booking attempt was not submitted",
                evidence={
                    "event_id": event_id,
                    "booking_ref": started.get("booking_ref"),
                    "customer_ref": customer_ref,
                    "started_at": started_at.isoformat(),
                    "age_minutes": age_minutes,
                    "confirmation_deadline": deadline.isoformat(),
                    "estimated_booking_value_rs": estimated_value,
                    "estimated": estimated_value is not None,
                },
                confidence="low",
                rs_impact=amount,
                suggested_action=(
                    "Follow up with the customer to help complete the booking."
                    if estimated_value is not None
                    else "Follow up with the customer and record booking amounts "
                    "to estimate its value."
                ),
                cause_known=False,
                dedupe_key=f"abandoned_booking:{event_id}",
            )
        )
    return findings
