"""Canonical Pakistan phone normalization: +92XXXXXXXXXX."""
from __future__ import annotations

import re
from typing import Optional


def normalize_phone(phone: str) -> Optional[str]:
    """
    Convert common PK phone inputs to +92XXXXXXXXXX.
    Accepts: 0300-1234567, 0300 1234567, +923001234567, 923001234567, 3001234567.
    Returns None if the number cannot be normalized.
    """
    if phone is None:
        return None
    raw = str(phone).strip()
    if not raw:
        return None

    digits = re.sub(r"\D", "", raw)

    if len(digits) == 11 and digits.startswith("0"):
        return "+92" + digits[1:]
    if len(digits) == 12 and digits.startswith("92"):
        return "+" + digits
    if len(digits) == 10:
        return "+92" + digits
    # Already international (+…) with enough digits — keep chatbot-compatible fallback
    if raw.startswith("+") and len(digits) >= 12:
        return "+" + digits

    return None
