"""Atomic slot claim / release for booking race-safety.

Prefers Postgres RPCs (migration 021). Falls back to filtered UPDATE if RPC
is not deployed yet so deploys stay safe before the SQL is run.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from supabase import create_client

from app.config import SUPABASE_URL, SUPABASE_KEY

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

SLOT_UNAVAILABLE_MSG = "This slot is no longer available, please choose another"

# Statuses that still occupy a pitch hour
ACTIVE_BOOKING_STATUSES = frozenset({"Pending", "Confirmed", "Reschedule"})
# Statuses that free the slot for rebooking
RELEASE_ON_STATUSES = frozenset({"Rejected", "No-show", "No Show", "Noshow", "Cancelled", "Canceled"})


def _rpc_json(name: str, params: Dict[str, Any]) -> Any:
    res = supabase.rpc(name, params).execute()
    return res.data


def claim_slot(booking_date: str, booking_time: str, phone: str) -> Optional[Dict[str, Any]]:
    """
    Atomically claim an Available slot for Date+Time.
    Returns the updated slot row (incl. id) if claim succeeded, else None.
    """
    d = str(booking_date or "").strip()
    t = str(booking_time or "").strip()
    p = str(phone or "").strip()
    if not d or not t:
        return None

    try:
        data = _rpc_json(
            "claim_slot_atomic",
            {"p_date": d, "p_time": t, "p_phone": p},
        )
        if data is None:
            return None
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        # Fallback until migration 021 is applied
        try:
            res = (
                supabase.table("slots")
                .update({
                    "Status": "Booked",
                    "Booked By": p or "HOLD",
                    "Phone": p or "HOLD",
                })
                .eq("Date", d)
                .eq("Time", t)
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
    if slot_id is None or not booking_id:
        return
    try:
        _rpc_json(
            "link_slot_booking_atomic",
            {"p_slot_id": str(slot_id), "p_booking_id": str(booking_id)},
        )
        return
    except Exception:
        pass
    try:
        supabase.table("slots").update({"Booking ID": booking_id}).eq("id", slot_id).execute()
    except Exception:
        pass


def release_slot(slot_id: Any) -> None:
    """Roll back a claimed slot so it is Available again."""
    if slot_id is None:
        return
    try:
        _rpc_json("release_slot_atomic", {"p_slot_id": str(slot_id)})
        return
    except Exception:
        pass
    try:
        supabase.table("slots").update({
            "Status": "Available",
            "Booked By": "EMPTY",
            "Phone": "EMPTY",
            "Booking ID": "",
        }).eq("id", slot_id).execute()
    except Exception:
        pass


def release_slot_by_datetime(
    booking_date: str,
    booking_time: str,
    booking_id: Optional[str] = None,
) -> None:
    """Free a Booked slot for this Date+Time (used on reject / delete / reschedule)."""
    d = str(booking_date or "").strip()
    t = str(booking_time or "").strip()
    if not d or not t:
        return
    try:
        _rpc_json(
            "release_slot_by_datetime",
            {
                "p_date": d,
                "p_time": t,
                "p_booking_id": str(booking_id or "") or None,
            },
        )
        return
    except Exception:
        pass
    try:
        q = (
            supabase.table("slots")
            .update({
                "Status": "Available",
                "Booked By": "EMPTY",
                "Phone": "EMPTY",
                "Booking ID": "",
            })
            .eq("Date", d)
            .eq("Time", t)
            .eq("Status", "Booked")
        )
        if booking_id:
            # Prefer ownership match; if Booking ID was never linked, still free by datetime
            pass
        q.execute()
    except Exception:
        pass


def is_unique_violation(err: Exception) -> bool:
    msg = str(err).lower()
    return (
        "duplicate key" in msg
        or "unique constraint" in msg
        or "idx_bookings_active_datetime" in msg
        or "idx_bookings_active_phone_date" in msg
        or "idempotency_key" in msg
    )
