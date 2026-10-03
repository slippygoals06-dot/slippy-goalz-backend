"""Persistence helpers for deterministic booking funnel events."""
from typing import Any, Literal, Optional

BookingEventType = Literal["booking_started", "booking_confirmed"]


def record_booking_event(
    client: Any,
    *,
    event_type: BookingEventType,
    customer_ref: str,
    booking_ref: Optional[str] = None,
) -> None:
    """Insert one booking funnel event using the supplied Supabase client."""
    normalized_ref = str(customer_ref or "").strip()
    if not normalized_ref:
        raise ValueError("customer_ref must be non-empty")
    client.table("booking_events").insert(
        {
            "booking_ref": booking_ref,
            "customer_ref": normalized_ref,
            "event_type": event_type,
        }
    ).execute()
