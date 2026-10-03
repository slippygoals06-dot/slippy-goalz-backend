"""Detect upcoming bookings with a required but unconfirmed deposit."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping, Optional
from zoneinfo import ZoneInfo

from app.manager.models import Finding

KARACHI = ZoneInfo("Asia/Karachi")


def _appointment_at(item: Mapping[str, Any]) -> Optional[datetime]:
    try:
        return datetime.strptime(
            f"{item['date']} {item['time']}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=KARACHI)
    except (KeyError, TypeError, ValueError):
        return None


def _deposit_amount(item: Mapping[str, Any]) -> Optional[Decimal]:
    value = item.get("deposit_amount")
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


def detect_unpaid_deposits(snapshot: Mapping[str, Any]) -> list[Finding]:
    """Report next-48-hour bookings with an explicitly requested unpaid deposit."""
    section = snapshot.get("unpaid_deposits") or {}
    items = list(section.get("items") or [])
    eligible = [
        (appointment, item, amount)
        for item in items
        if item.get("deposit_paid") is not True
        and (appointment := _appointment_at(item)) is not None
        and (amount := _deposit_amount(item)) is not None
    ]
    if not eligible:
        return []

    eligible.sort(key=lambda entry: entry[0])
    as_of = str(snapshot.get("as_of") or "")
    confidence: Literal["high", "medium", "low"] = "medium"
    findings = []
    for appointment, item, amount in eligible:
        booking_ref = str(item.get("booking_ref") or "").strip()
        customer_ref = str(item.get("customer_ref") or "").strip()
        dedupe_ref = booking_ref or (
            f"{appointment.date().isoformat()}:{appointment.strftime('%H:%M')}:"
            f"{customer_ref}"
        )
        findings.append(
            Finding(
                type="unpaid_deposits",
                severity="yellow",
                title="Upcoming booking has an unconfirmed advance",
                evidence={
                    "as_of": as_of,
                    "window_hours": 48,
                    "booking_ref": booking_ref or None,
                    "customer_ref": customer_ref or None,
                    "appointment_at": appointment.isoformat(),
                    "deposit_amount_rs": amount,
                    "deposit_paid": False,
                    "deposit_paid_source": "bookings.deposit_paid",
                },
                confidence=confidence,
                rs_impact=amount,
                expires_at=appointment,
                suggested_action=(
                    "Confirm the advance with the customer or update the booking "
                    "when payment is received."
                ),
                cause_known=False,
                dedupe_key=f"unpaid_deposit:{dedupe_ref}",
            )
        )
    return findings
