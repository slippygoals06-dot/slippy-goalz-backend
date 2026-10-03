"""Types shared by deterministic Manager detectors."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


@dataclass(frozen=True)
class Finding:
    """One deterministic finding with supporting evidence and impact."""

    type: str
    severity: Literal["red", "yellow", "green"]
    title: str
    evidence: dict[str, Any]
    confidence: Literal["high", "medium", "low"]
    rs_impact: Decimal
    expires_at: datetime | None = None
    suggested_action: str | None = None
    cause_known: bool = False
    dedupe_key: str = ""
