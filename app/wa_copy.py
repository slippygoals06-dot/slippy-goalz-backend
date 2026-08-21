"""Slippy Goalz Arena — WhatsApp copy helpers (confirm / remind body params)."""
from __future__ import annotations

from typing import Any, Dict, List


def format_session_time(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "—"
    # Prefer compact 24h HH:MM if already stored that way
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return s


def format_session_date(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "—"
    return s[:10]


def confirm_body_params(booking: Dict[str, Any]) -> List[str]:
    """
    Meta template body vars for booking_confirmed.
    Expected template (example):
      Hi {{1}}, your Slippy Goalz Arena pitch is confirmed for {{2}} at {{3}}.
      Arrive 5 mins early. See you on the pitch!
    """
    name = (booking.get("Name") or "player").strip() or "player"
    date_s = format_session_date(booking.get("Date"))
    time_s = format_session_time(booking.get("Time"))
    return [name, date_s, time_s]


def reminder_body_params(booking: Dict[str, Any]) -> List[str]:
    """Same {{1}} name {{2}} date {{3}} time shape for reminder templates."""
    return confirm_body_params(booking)
