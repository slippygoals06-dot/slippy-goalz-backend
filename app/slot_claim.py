"""Atomic slot claim / release for booking race-safety."""
from __future__ import annotations

from typing import Any, Dict, Optional

from supabase import create_client

from app.config import SUPABASE_URL, SUPABASE_KEY

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

SLOT_UNAVAILABLE_MSG = "This slot is no longer available, please choose another"


def claim_slot(booking_date: str, booking_time: str, phone: str) -> Optional[Dict[str, Any]]:
    """
    Atomically claim an Available slot for Date+Time.
    Returns the updated slot row (incl. id) if claim succeeded, else None.
    """
    try:
        res = (
            supabase.table("slots")
            .update({
                "Status": "Booked",
                "Booked By": phone,
                "Phone": phone,
            })
            .eq("Date", booking_date)
            .eq("Time", booking_time)
            .eq("Status", "Available")
            .execute()
        )
        if res.data and len(res.data) > 0:
            return res.data[0]
        return None
    except Exception:
        return None


def link_slot_booking(slot_id: Any, booking_id: str) -> None:
    """Attach booking ID to an already-claimed slot."""
    supabase.table("slots").update({"Booking ID": booking_id}).eq("id", slot_id).execute()


def release_slot(slot_id: Any) -> None:
    """Roll back a claimed slot so it is Available again."""
    try:
        supabase.table("slots").update({
            "Status": "Available",
            "Booked By": "EMPTY",
            "Phone": "EMPTY",
            "Booking ID": "",
        }).eq("id", slot_id).execute()
    except Exception:
        pass
