"""Detect available slots in the next 48 hours."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping, Optional
from zoneinfo import ZoneInfo

from app.manager.models import Finding

KARACHI = ZoneInfo("Asia/Karachi")


def _slot_expiry(slot: Mapping[str, Any]) -> Optional[datetime]:
    try:
        return datetime.strptime(
            f"{slot['date']} {slot['time']}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=KARACHI)
    except (KeyError, TypeError, ValueError):
        return None


def _potential_value(slot: Mapping[str, Any]) -> Optional[Decimal]:
    value = slot.get("potential_rs")
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount >= 0 else None


def detect_empty_slots_next_48h(
    snapshot: Mapping[str, Any],
) -> list[Finding]:
    """Report near-term available slots with historical value estimates."""
    section = snapshot.get("next_48h_empty_slots") or {}
    slots = list(section.get("slots") or [])
    if not slots:
        return []

    expiring_slots = [
        (expiry, slot)
        for slot in slots
        if (expiry := _slot_expiry(slot)) is not None
    ]
    if not expiring_slots:
        return []

    expiring_slots.sort(key=lambda item: item[0])
    values = [_potential_value(slot) for slot in slots]
    value_known = all(value is not None for value in values)
    impact = sum(
        (value for value in values if value is not None),
        Decimal("0"),
    )
    sample_size = 0
    confidence: Literal["high", "medium", "low"] = "low"
    suggested_action = (
        "Review and promote these open slots; values are historical estimates."
        if value_known
        else "Record completed booking amounts to estimate these slots, then review promotion."
    )
    expires_at, _first_slot = expiring_slots[0]
    as_of = str(snapshot.get("as_of") or "")
    dedupe_day = as_of[:10] or expires_at.date().isoformat()

    return [
        Finding(
            type="empty_slots_next_48h",
            severity="yellow",
            title=f"{len(slots)} available slot(s) in the next 48 hours",
            evidence={
                "as_of": as_of,
                "window_hours": 48,
                "empty_slot_count": len(slots),
                "slots": slots,
                "potential_rs_total": impact if value_known else None,
                "estimated": value_known and all(
                    slot.get("estimated") is True for slot in slots
                ),
                "known_estimate_count": sum(value is not None for value in values),
                "sample_size": sample_size,
                "distance_from_normal_pct": None,
            },
            confidence=confidence,
            rs_impact=impact,
            expires_at=expires_at,
            suggested_action=suggested_action,
            cause_known=False,
            dedupe_key=f"empty_slots_next_48h:{dedupe_day}",
        )
    ]
