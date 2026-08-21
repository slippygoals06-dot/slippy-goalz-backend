"""Pitch session pricing helpers for Slippy Goalz Arena.

Do not use phone-repair catalogs here — amounts should come from booking.amount
or explicit arena rates.
"""
from __future__ import annotations

from typing import Optional

# Default 1-hour pitch rate (PKR). Override via booking.amount in production.
DEFAULT_PITCH_RATE = 4000

PLAYER_BAND_RATES = {
    "5-a-side": 3000,
    "7-a-side": 4000,
    "11-a-side": 6000,
}


def calculate_booking_amount(
    device: Optional[str] = None,
    service: Optional[str] = None,
) -> Optional[float]:
    """
    Estimate amount from session type. Returns None when unknown so callers
    never invent a repair-shop price.
    """
    label = " ".join(str(x or "") for x in (device, service)).lower()
    for key, rate in PLAYER_BAND_RATES.items():
        if key.replace("-", " ") in label or key in label:
            return float(rate)
    if service and "pitch" in str(service).lower():
        return float(DEFAULT_PITCH_RATE)
    return None
