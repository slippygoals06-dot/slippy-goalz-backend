"""Server-side tier pricing — mirrors PublicBooking.jsx TIER_PRICES / DEVICES."""
from __future__ import annotations

from typing import Dict, Optional

# Device model → tier (same list as dashboard public booking form)
DEVICE_TIERS: Dict[str, str] = {
    "iphone 16 pro max": "Flagship",
    "iphone 16 pro": "Flagship",
    "iphone 16": "Premium",
    "iphone 15 pro max": "Flagship",
    "iphone 15 pro": "Flagship",
    "iphone 15": "Premium",
    "iphone 14 pro max": "Flagship",
    "iphone 14 pro": "Premium",
    "iphone 14": "Premium",
    "iphone 13 pro max": "Premium",
    "iphone 13 pro": "Premium",
    "iphone 13": "Mid",
    "iphone 12 pro max": "Premium",
    "iphone 12 pro": "Mid",
    "iphone 12": "Mid",
    "iphone 11 pro max": "Mid",
    "iphone 11": "Mid",
    "iphone se (2022)": "Budget",
    "iphone xr": "Budget",
    "samsung galaxy s24 ultra": "Flagship",
    "samsung galaxy s24": "Premium",
    "samsung galaxy s23 ultra": "Flagship",
    "samsung galaxy s23": "Premium",
    "samsung galaxy s22": "Mid",
    "samsung galaxy a54": "Budget",
    "samsung galaxy a34": "Budget",
}

TIER_PRICES: Dict[str, Dict[str, int]] = {
    "Budget": {
        "Screen Repair": 3000,
        "Battery Replacement": 1500,
        "Software Fix": 1000,
        "Water Damage": 5000,
        "Charging Port": 1500,
        "Camera Repair": 2000,
    },
    "Mid": {
        "Screen Repair": 6000,
        "Battery Replacement": 2000,
        "Software Fix": 1500,
        "Water Damage": 7000,
        "Charging Port": 2000,
        "Camera Repair": 3000,
    },
    "Premium": {
        "Screen Repair": 10000,
        "Battery Replacement": 3000,
        "Software Fix": 1500,
        "Water Damage": 9000,
        "Charging Port": 2500,
        "Camera Repair": 4000,
    },
    "Flagship": {
        "Screen Repair": 15000,
        "Battery Replacement": 4500,
        "Software Fix": 2000,
        "Water Damage": 12000,
        "Charging Port": 3000,
        "Camera Repair": 5500,
    },
}


def resolve_device_tier(device: Optional[str]) -> Optional[str]:
    if not device or not str(device).strip():
        return None
    return DEVICE_TIERS.get(str(device).strip().lower())


def calculate_booking_amount(
    device: Optional[str],
    service: Optional[str],
) -> Optional[float]:
    """
    Recalculate amount from device tier + service.
    Returns None when device/service are unknown (caller should omit amount,
    never trust a client-supplied figure).
    """
    tier = resolve_device_tier(device)
    if not tier or not service:
        return None
    prices = TIER_PRICES.get(tier) or {}
    price = prices.get(str(service).strip())
    if price is None:
        return None
    return float(price)
