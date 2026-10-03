"""Detect no-show rates materially above the weekday baseline."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, Mapping, Optional

from app.manager.models import Finding


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def detect_no_show_rate(snapshot: Mapping[str, Any]) -> list[Finding]:
    """Flag a no-show rate more than 25% above its same-weekday baseline."""
    today = snapshot.get("today") or {}
    baseline = snapshot.get("baseline") or {}
    sample_size = int(baseline.get("sample_size") or 0)
    today_no_shows = Decimal(str(today.get("no_shows") or 0))
    today_completed = Decimal(str(today.get("completed") or 0))
    today_eligible = today_no_shows + today_completed
    baseline_no_shows = _decimal(baseline.get("no_shows"))
    baseline_completed = _decimal(baseline.get("completed"))

    if (
        sample_size < 2
        or today_eligible <= 0
        or baseline_no_shows is None
        or baseline_completed is None
    ):
        return []

    baseline_eligible = baseline_no_shows + baseline_completed
    if baseline_eligible <= 0:
        return []

    today_rate = today_no_shows / today_eligible
    baseline_rate = baseline_no_shows / baseline_eligible
    if baseline_rate == 0:
        if today_rate == 0:
            return []
        deviation_pct = None
        exceeds_threshold = True
    else:
        deviation_pct = ((today_rate - baseline_rate) / baseline_rate) * 100
        exceeds_threshold = deviation_pct > Decimal("25")
    if not exceeds_threshold:
        return []

    if sample_size < 3:
        confidence: Literal["high", "medium", "low"] = "low"
    elif deviation_pct is not None and deviation_pct > Decimal("40") and sample_size >= 4:
        confidence = "high"
    else:
        confidence = "medium"

    baseline_revenue = _decimal(baseline.get("revenue"))
    revenue_sample_size = int(
        baseline.get("revenue_sample_size", sample_size) or 0
    )
    estimated_lost_rs = Decimal("0")
    if (
        baseline_revenue is not None
        and baseline_completed > 0
        and revenue_sample_size == sample_size
    ):
        average_completed_value = baseline_revenue / baseline_completed
        estimated_lost_rs = (average_completed_value * today_no_shows).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )

    as_of = str(snapshot.get("as_of") or "")
    finding_day = as_of[:10] or "unknown"
    severity: Literal["red", "yellow", "green"] = (
        "red"
        if deviation_pct is not None
        and deviation_pct > Decimal("40")
        and sample_size >= 4
        else "yellow"
    )
    return [
        Finding(
            type="no_show_rate",
            severity=severity,
            title="No-show rate is above the weekday baseline",
            evidence={
                "as_of": as_of,
                "today_no_shows": today_no_shows,
                "today_completed": today_completed,
                "today_eligible_bookings": today_eligible,
                "today_rate_pct": today_rate * 100,
                "baseline_no_shows_per_day": baseline_no_shows,
                "baseline_completed_per_day": baseline_completed,
                "baseline_eligible_per_day": baseline_eligible,
                "baseline_rate_pct": baseline_rate * 100,
                "relative_deviation_pct": deviation_pct,
                "baseline_sample_size": sample_size,
                "estimated_lost_rs": estimated_lost_rs,
            },
            confidence=confidence,
            rs_impact=estimated_lost_rs,
            expires_at=None,
            suggested_action="Review no-show follow-up and reminder practices for today’s bookings.",
            cause_known=False,
            dedupe_key=f"no_show_rate:{finding_day}",
        )
    ]
