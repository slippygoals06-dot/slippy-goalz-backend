"""Run Manager detectors and persist their current open findings."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import UUID

from supabase import create_client

from app.config import SUPABASE_KEY, SUPABASE_URL
from app.manager import SINGLETON_BUSINESS_ID
from app.manager.detectors.abandoned_bookings import (
    confirmed_booking_start_ids,
    detect_abandoned_bookings,
)
from app.manager.detectors.empty_slots_next_48h import detect_empty_slots_next_48h
from app.manager.detectors.no_show_rate import detect_no_show_rate
from app.manager.detectors.revenue_vs_weekday_avg import (
    detect_revenue_vs_weekday_avg,
)
from app.manager.detectors.unpaid_deposits import detect_unpaid_deposits
from app.manager.models import Finding
from app.manager.snapshot import build_snapshot

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def normalize_business_id(business_id: str) -> str:
    """Validate and normalize the configured single-business UUID."""
    try:
        normalized = str(UUID(str(business_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("business_id must be the configured singleton UUID") from exc
    if normalized != SINGLETON_BUSINESS_ID:
        raise ValueError("business_id does not belong to this single-business MVP")
    return normalized


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _list_open_findings(business_id: str) -> list[dict[str, Any]]:
    """Return every open finding, paging through PostgREST's row limit."""
    findings: list[dict[str, Any]] = []
    start = 0
    page_size = 1000
    while True:
        result = (
            supabase.table("findings")
            .select("*")
            .eq("business_id", business_id)
            .eq("status", "open")
            .range(start, start + page_size - 1)
            .execute()
        )
        page = result.data or []
        findings.extend(page)
        if len(page) < page_size:
            return findings
        start += page_size


def _finding_payload(business_id: str, finding: Finding) -> dict[str, Any]:
    if not finding.dedupe_key.strip():
        raise ValueError("Every finding must have a non-empty dedupe_key")
    return {
        "business_id": business_id,
        "type": finding.type,
        "severity": finding.severity,
        "title": finding.title,
        "evidence_json": _json_safe(finding.evidence),
        "confidence": finding.confidence,
        "cause_known": finding.cause_known,
        "rs_impact": str(finding.rs_impact),
        "expires_at": finding.expires_at.isoformat() if finding.expires_at else None,
        "suggested_action": finding.suggested_action,
        "status": "open",
        "dedupe_key": finding.dedupe_key,
    }


def _eligible_findings(findings: list[Finding]) -> list[Finding]:
    """Drop findings with neither quantified impact nor a useful next action."""
    return [
        finding
        for finding in findings
        if finding.rs_impact != 0 or bool((finding.suggested_action or "").strip())
    ]


def _upsert_open_finding(business_id: str, finding: Finding) -> dict[str, Any]:
    payload = _finding_payload(business_id, finding)
    result = supabase.rpc(
        "manager_upsert_open_finding",
        {
            "p_business_id": payload["business_id"],
            "p_type": payload["type"],
            "p_severity": payload["severity"],
            "p_title": payload["title"],
            "p_evidence_json": payload["evidence_json"],
            "p_confidence": payload["confidence"],
            "p_cause_known": payload["cause_known"],
            "p_rs_impact": payload["rs_impact"],
            "p_expires_at": payload["expires_at"],
            "p_suggested_action": payload["suggested_action"],
            "p_dedupe_key": payload["dedupe_key"],
        },
    ).execute()
    if not result.data:
        raise RuntimeError("Finding upsert returned no row")
    return result.data[0]


def _impact(row: Mapping[str, Any]) -> Decimal:
    try:
        return Decimal(str(row.get("rs_impact") or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _expiry(row: Mapping[str, Any]) -> datetime:
    value = row.get("expires_at")
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _state_from_deviation(
    deviation: Any,
    sample_size: int,
) -> str:
    if deviation is None:
        return "NORMAL"
    value = Decimal(str(deviation))
    if value < Decimal("-40") and sample_size >= 4:
        return "AT_RISK"
    if value < Decimal("-25"):
        return "BELOW_NORMAL"
    return "NORMAL"


def _business_state(
    snapshot: Mapping[str, Any],
    findings: list[Finding],
) -> tuple[dict[str, str], dict[str, bool]]:
    baseline = snapshot.get("baseline") or {}
    deviations = snapshot.get("vs_normal") or {}
    sample_size = int(baseline.get("sample_size") or 0)
    revenue_sample_size = int(baseline.get("revenue_sample_size") or 0)
    today = snapshot.get("today") or {}
    available_slots = int(
        (snapshot.get("next_48h_empty_slots") or {}).get("count") or 0
    )

    tracking = {
        "Revenue": (
            today.get("revenue") is not None and revenue_sample_size >= 2
        ),
        "Bookings": sample_size >= 2,
        "Payments": bool(
            (snapshot.get("tracking") or {}).get("unpaid_deposits")
        ),
        "Capacity": bool(
            (snapshot.get("tracking") or {}).get("empty_slots", True)
        ),
        "Customers": bool((snapshot.get("tracking") or {}).get("customers")),
    }
    state = {
        "Revenue": _state_from_deviation(
            deviations.get("revenue"),
            revenue_sample_size,
        ),
        "Bookings": _state_from_deviation(
            deviations.get("bookings"),
            sample_size,
        ),
        "Payments": "NORMAL",
        "Capacity": "BELOW_NORMAL" if available_slots else "NORMAL",
        "Customers": "NORMAL",
    }
    no_show_finding = next(
        (finding for finding in findings if finding.type == "no_show_rate"),
        None,
    )
    if no_show_finding:
        state["Bookings"] = (
            "AT_RISK"
            if no_show_finding.severity == "red"
            else "BELOW_NORMAL"
        )
    if any(finding.type == "unpaid_deposits" for finding in findings):
        state["Payments"] = "AT_RISK"
    return state, tracking


def run_detectors(business_id: str) -> dict[str, Any]:
    """Build a snapshot, persist detector findings, and derive business state."""
    normalized_id = normalize_business_id(business_id)
    snapshot = build_snapshot(normalized_id)
    detected = _eligible_findings(
        [
            *detect_abandoned_bookings(snapshot),
            *detect_empty_slots_next_48h(snapshot),
            *detect_no_show_rate(snapshot),
            *detect_revenue_vs_weekday_avg(snapshot),
            *detect_unpaid_deposits(snapshot),
        ]
    )
    for finding in detected:
        _upsert_open_finding(normalized_id, finding)
    findings = _list_open_findings(normalized_id)
    confirmed_starts = confirmed_booking_start_ids(snapshot)
    active_dedupe_keys = {finding.dedupe_key for finding in detected}
    for row in findings:
        dedupe_key = str(row.get("dedupe_key") or "")
        resolution = None
        if row.get("type") == "abandoned_bookings":
            event_id = dedupe_key.removeprefix("abandoned_booking:")
            confirmed_at = confirmed_starts.get(event_id)
            if confirmed_at:
                resolution = {
                    "decided_at": confirmed_at,
                    "verified_result": {
                        "result": "booking request submitted",
                        "confirmed_at": confirmed_at,
                    },
                }
        elif (
            row.get("type") == "unpaid_deposits"
            and dedupe_key not in active_dedupe_keys
        ):
            resolution = {
                "decided_at": snapshot["as_of"],
                "verified_result": {
                    "result": "deposit is no longer outstanding in the next 48 hours"
                },
            }
        if resolution is None:
            continue
        supabase.table("findings").update(
            {
                "status": "done",
                **resolution,
            }
        ).eq("id", row["id"]).eq("status", "open").execute()
        row["status"] = "done"
    findings = [row for row in findings if row.get("status") == "open"]
    findings.sort(key=lambda row: (-_impact(row), _expiry(row)))
    business_state, state_tracking = _business_state(snapshot, detected)
    return {
        "findings": findings,
        "business_state": business_state,
        "business_state_tracking": state_tracking,
        "snapshot": snapshot,
    }
