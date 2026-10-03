"""Detect material revenue deviation from the same-weekday baseline."""
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping, Optional

from app.manager.models import Finding


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def detect_revenue_vs_weekday_avg(
    snapshot: Mapping[str, Any],
) -> list[Finding]:
    """Flag revenue more than 25% above or below its weekday average."""
    today = snapshot.get("today") or {}
    baseline = snapshot.get("baseline") or {}
    revenue_sample_size = int(
        baseline.get("revenue_sample_size", baseline.get("sample_size", 0)) or 0
    )
    actual = _decimal(today.get("revenue"))
    normal = _decimal(baseline.get("revenue"))
    if actual is None or normal is None or revenue_sample_size < 2 or normal == 0:
        return []

    deviation_pct = ((actual - normal) / abs(normal)) * 100
    if abs(deviation_pct) <= Decimal("25"):
        return []

    if revenue_sample_size < 3:
        confidence: Literal["high", "medium", "low"] = "low"
    elif abs(deviation_pct) > Decimal("40") and revenue_sample_size >= 4:
        confidence = "high"
    else:
        confidence = "medium"

    below_normal = deviation_pct < 0
    severity: Literal["red", "yellow", "green"] = (
        "red"
        if below_normal
        and abs(deviation_pct) > Decimal("40")
        and revenue_sample_size >= 4
        else "yellow"
        if below_normal
        else "green"
    )
    impact = max(normal - actual, Decimal("0"))
    action = (
        "Review booking volume and cancellations to identify the revenue shortfall."
        if below_normal
        else "Review what drove above-normal revenue and repeat the successful practices."
    )
    as_of = str(snapshot.get("as_of") or "")
    finding_day = as_of[:10] or "unknown"

    return [
        Finding(
            type="revenue_vs_weekday_avg",
            severity=severity,
            title=(
                "Revenue is below the weekday baseline"
                if below_normal
                else "Revenue is above the weekday baseline"
            ),
            evidence={
                "as_of": as_of,
                "today_revenue_rs": actual,
                "weekday_average_revenue_rs": normal,
                "deviation_pct": deviation_pct,
                "threshold_pct": Decimal("25"),
                "revenue_sample_size": revenue_sample_size,
            },
            confidence=confidence,
            rs_impact=impact,
            expires_at=None,
            suggested_action=action,
            cause_known=False,
            dedupe_key=f"revenue_vs_weekday_avg:{finding_day}",
        )
    ]
