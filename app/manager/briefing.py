"""Validated, privacy-limited owner briefings for deterministic findings."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from app.manager.run import _json_safe

logger = logging.getLogger(__name__)
KARACHI = ZoneInfo("Asia/Karachi")
_NUMBER_RE = re.compile(r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_CAUSAL_RE = re.compile(
    r"\b(?:because|caused by|cause of|the cause (?:is|was)|due to|"
    r"reason (?:is|for)|result(?:ed)? in|led to|leads to|therefore|"
    r"as a result|driven by|likely due to|probably due to|stem(?:s|med|ming) "
    r"from)\b",
    re.IGNORECASE,
)
_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_CACHE_LOCK = Lock()
_RULE_CONSTANTS = {"2", "24", "48"}
_SAFE_ACTIONS = {
    "Review no-show follow-up and reminder practices for today’s bookings.",
    "Review booking volume and cancellations to identify the revenue shortfall.",
    "Review what drove above-normal revenue and repeat the successful practices.",
    "Review and promote these open slots; values are historical estimates.",
    "Record completed booking amounts to estimate these slots, then review promotion.",
    "Follow up with the customer to help complete the booking.",
    "Follow up with the customer and record booking amounts to estimate its value.",
    "Confirm the advance with the customer or update the booking when payment is received.",
}

SYSTEM_PROMPT = """You write a concise owner briefing from deterministic input.
Return one JSON object with exactly this shape:
{"headline":"...", "cards":[{"finding_id":"...", "plain_text":"...",
"why_it_matters":"...", "next_step":"..."}], "leave_alone_note":"..."}
Use only provided numbers and copy rs_impact_display, counts_display,
pct_display, and expires_at_local values exactly when using them. Never
calculate, round, convert, or introduce a number. Include no new findings.
Rank cards by rs_impact_display descending. Do not invent causes. When a finding
has cause_known=false, include the exact phrase "can't tell yet" in its card and
do not make a causal claim. Use no customer names or message content. Copy only
the supplied finding_id values. Follow the input language and keep the briefing
short. The constants 2h, 24h, and 48h may be used only where relevant."""


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _display_money(value: Any) -> str:
    amount = _decimal(value)
    if amount is None:
        return "Not available"
    rounded = amount.quantize(Decimal("1"))
    return f"Rs {rounded:,.0f}"


def _local_time(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KARACHI)
    return parsed.astimezone(KARACHI).strftime("%Y-%m-%d %H:%M PKT")


def _safe_evidence(value: Any, key: str = "") -> Any:
    """Keep deterministic numbers, timestamps, and references, not prose."""
    normalized = key.lower()
    if any(part in normalized for part in ("name", "message", "phone", "email")):
        return None
    if isinstance(value, Mapping):
        result = {}
        for child_key, child_value in value.items():
            safe = _safe_evidence(child_value, str(child_key))
            if safe is not None:
                result[str(child_key)] = safe
        return result
    if isinstance(value, (list, tuple)):
        return [
            safe
            for item in value
            if (safe := _safe_evidence(item, key)) is not None
        ]
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        if any(part in normalized for part in ("ref", "id")):
            return value
        if (
            normalized.endswith("_at")
            or normalized in {"date", "time", "as_of", "window_hours"}
            or value in {"same_weekday_4_week_average", "overall_4_week_average"}
        ):
            return value
        if normalized in {
            "severity", "confidence", "type", "status", "result", "estimated"
        }:
            return value
        return None
    return None


def _snapshot_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    today = snapshot.get("today") or {}
    baseline = snapshot.get("baseline") or {}
    vs_normal = snapshot.get("vs_normal") or {}
    slots = snapshot.get("next_48h_empty_slots") or {}
    deposits = snapshot.get("unpaid_deposits") or {}
    return _json_safe(
        {
            "today": {
                "revenue": today.get("revenue"),
                "bookings": today.get("bookings"),
                "completed": today.get("completed"),
                "cancelled": today.get("cancelled"),
                "no_shows": today.get("no_shows"),
            },
            "baseline": {
                "revenue": baseline.get("revenue"),
                "bookings": baseline.get("bookings"),
                "sample_size": baseline.get("sample_size"),
                "revenue_sample_size": baseline.get("revenue_sample_size"),
            },
            "vs_normal": vs_normal,
            "empty_slots_next_48h": {
                "count": slots.get("count"),
            },
            "unpaid_deposits_next_48h": {
                "count": deposits.get("count"),
            },
            "funnel": {
                "chat_started": (snapshot.get("funnel") or {}).get("chat_started"),
                "booking_started": (snapshot.get("funnel") or {}).get("booking_started"),
                "paid": (snapshot.get("funnel") or {}).get("paid"),
            },
        }
    )


def _display_finding(row: Mapping[str, Any]) -> dict[str, Any]:
    impact = row.get("rs_impact")
    evidence = row.get("evidence_json") or {}
    safe_evidence = _safe_evidence(evidence)
    if not isinstance(safe_evidence, dict):
        safe_evidence = {}
    counts = {
        key: value
        for key, value in safe_evidence.items()
        if any(word in key.lower() for word in ("count", "sample_size"))
    }
    percentages = {
        key: value
        for key, value in safe_evidence.items()
        if any(word in key.lower() for word in ("pct", "percent", "rate"))
    }
    return {
        "finding_id": str(row.get("id") or ""),
        "type": row.get("type"),
        "severity": row.get("severity"),
        "confidence": row.get("confidence"),
        "cause_known": bool(row.get("cause_known")),
        "rs_impact": str(impact) if impact is not None else None,
        "rs_impact_display": _display_money(impact),
        "counts": counts,
        "counts_display": {
            key: f"{int(value):,}" if _decimal(value) is not None else str(value)
            for key, value in counts.items()
        },
        "pct": percentages,
        "pct_display": {
            key: f"{_decimal(value):.2f}%"
            if _decimal(value) is not None
            else str(value)
            for key, value in percentages.items()
        },
        "expires_at_local": _local_time(row.get("expires_at")),
        "suggested_action": (
            row.get("suggested_action")
            if row.get("suggested_action") in _SAFE_ACTIONS
            else None
        ),
        "evidence": safe_evidence,
    }


def build_llm_input(
    snapshot: Mapping[str, Any],
    findings: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the compact prompt payload without customer free-text content."""
    prepared_findings = [_display_finding(row) for row in findings]
    prepared_findings.sort(
        key=lambda row: (
            -(_decimal(row.get("rs_impact")) or Decimal("0")),
            row.get("expires_at_local") or "9999",
        )
    )
    return {
        "snapshot_summary": _snapshot_summary(snapshot),
        "findings": prepared_findings,
        "rule_constants": ["2h", "24h", "48h"],
    }


def _normalized_number(value: str) -> str:
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return value
    return format(number.normalize(), "f")


def _allowed_numbers(payload: Mapping[str, Any]) -> set[str]:
    allowed = set(_RULE_CONSTANTS)

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                normalized_key = str(child_key).lower()
                if (
                    child_key == "finding_id"
                    or "ref" in normalized_key
                    or "id" in normalized_key
                ):
                    continue
                visit(child_value, str(child_key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, key)
        elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            allowed.add(_normalized_number(str(value)))
        elif isinstance(value, str):
            for match in _NUMBER_RE.findall(value):
                allowed.add(_normalized_number(match))

    visit(payload)
    return allowed


def _output_texts(output: Mapping[str, Any]) -> list[str]:
    texts = [output["headline"], output["leave_alone_note"]]
    for card in output["cards"]:
        texts.extend(
            [card["plain_text"], card["why_it_matters"], card["next_step"]]
        )
    return texts


def validate_briefing(
    output: Any,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate structure, references, causality, and all generated numbers."""
    if not isinstance(output, dict) or set(output) != {
        "headline", "cards", "leave_alone_note"
    }:
        raise ValueError("Briefing JSON has an invalid top-level shape")
    if (
        not isinstance(output["headline"], str)
        or not isinstance(output["leave_alone_note"], str)
        or not isinstance(output["cards"], list)
    ):
        raise ValueError("Briefing fields have invalid types")

    findings = {
        str(row["finding_id"]): row
        for row in payload.get("findings", [])
        if row.get("finding_id")
    }
    seen_ids: set[str] = set()
    impacts: list[Decimal] = []
    for card in output["cards"]:
        if not isinstance(card, dict) or set(card) != {
            "finding_id", "plain_text", "why_it_matters", "next_step"
        }:
            raise ValueError("Briefing card has an invalid shape")
        if not all(
            isinstance(card[field], str)
            for field in ("finding_id", "plain_text", "why_it_matters", "next_step")
        ):
            raise ValueError("Briefing card fields must be strings")
        finding_id = card["finding_id"]
        if finding_id not in findings:
            raise ValueError("Briefing references an unknown finding_id")
        if finding_id in seen_ids:
            raise ValueError("Briefing contains duplicate finding_ids")
        seen_ids.add(finding_id)
        impacts.append(
            _decimal(findings[finding_id].get("rs_impact")) or Decimal("0")
        )
        card_text = " ".join(
            card[field] for field in ("plain_text", "why_it_matters", "next_step")
        )
        if not findings[finding_id].get("cause_known"):
            if _CAUSAL_RE.search(card_text):
                raise ValueError("Causal wording is not allowed for unknown causes")
            if "can't tell yet" not in card_text.lower():
                raise ValueError("Unknown cause requires 'can't tell yet'")

    if any(
        impacts[index] < impacts[index + 1]
        for index in range(len(impacts) - 1)
    ):
        raise ValueError("Briefing cards are not ranked by rs_impact")
    if any(not row.get("cause_known") for row in findings.values()):
        if _CAUSAL_RE.search(
            " ".join([output["headline"], output["leave_alone_note"]])
        ):
            raise ValueError("Causal wording is not allowed for unknown causes")

    allowed = _allowed_numbers(payload)
    for text in _output_texts(output):
        for match in _NUMBER_RE.findall(text):
            if _normalized_number(match) not in allowed:
                raise ValueError(f"Briefing contains an unsupported number: {match}")
    return output


def _template_briefing(findings: list[Mapping[str, Any]]) -> dict[str, Any]:
    cards = []
    for row in findings:
        finding_id = str(row.get("id") or "")
        evidence = _safe_evidence(row.get("evidence_json") or {})
        impact = _display_money(row.get("rs_impact"))
        action = str(row.get("suggested_action") or "Review this finding.")
        cause_note = (
            "The cause is supported by the available evidence."
            if row.get("cause_known")
            else "Can't tell yet; the available evidence does not establish why."
        )
        cards.append(
            {
                "finding_id": finding_id,
                "plain_text": (
                    f"{row.get('title') or row.get('type')}: estimated impact "
                    f"{impact}."
                ),
                "why_it_matters": cause_note,
                "next_step": action,
            }
        )
    return {
        "headline": (
            "Here are the highest-impact items to review."
            if cards
            else "Everything looks steady."
        ),
        "cards": cards,
        "leave_alone_note": (
            "No action is needed right now." if not cards else ""
        ),
    }


def _leave_alone_briefing() -> dict[str, Any]:
    return {
        "headline": "Everything looks steady.",
        "cards": [],
        "leave_alone_note": "Leave everything alone for now; there is no evidenced action to take.",
    }


def _cache_key(
    payload: Mapping[str, Any],
    as_of: Optional[datetime],
) -> tuple[str, str, str]:
    local_as_of = as_of or datetime.now(KARACHI)
    if local_as_of.tzinfo is None:
        local_as_of = local_as_of.replace(tzinfo=KARACHI)
    local_as_of = local_as_of.astimezone(KARACHI)
    day = local_as_of.date().isoformat()
    bucket = f"{local_as_of.hour:02d}:{(local_as_of.minute // 30) * 30:02d}"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return day, bucket, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_of(snapshot: Mapping[str, Any]) -> datetime:
    value = snapshot.get("as_of")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(KARACHI)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KARACHI)
    return parsed.astimezone(KARACHI)


def _unexpired_findings(
    findings: list[Mapping[str, Any]],
    as_of: datetime,
) -> list[Mapping[str, Any]]:
    """Drop findings whose expiration timestamp is before the Karachi now."""
    active = []
    for finding in findings:
        value = finding.get("expires_at")
        if value:
            try:
                expires_at = datetime.fromisoformat(
                    str(value).replace("Z", "+00:00")
                )
            except ValueError:
                expires_at = None
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=KARACHI)
                if expires_at.astimezone(KARACHI) < as_of:
                    continue
        active.append(finding)
    return active


def generate_briefing(
    snapshot: Mapping[str, Any],
    findings: list[Mapping[str, Any]],
    groq_client: Any,
    model: str,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """Generate, validate, cache, or deterministically template a briefing."""
    as_of = _as_of(snapshot)
    active_findings = _unexpired_findings(findings, as_of)
    actionable = [
        row for row in active_findings
        if (_decimal(row.get("rs_impact")) or Decimal("0")) > 0
        or bool(str(row.get("suggested_action") or "").strip())
    ]
    if not actionable:
        result = {
            **_leave_alone_briefing(),
            "source": "fallback",
            "retry_count": 0,
        }
        logger.info(
            "Manager briefing source=%s retries=%s cache=miss",
            result["source"],
            result["retry_count"],
        )
        return result

    payload = build_llm_input(snapshot, actionable)
    cache_key = _cache_key(payload, as_of)
    if not refresh:
        with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
        if cached:
            logger.info(
                "Manager briefing source=%s retries=%s cache=hit",
                cached["source"],
                cached["retry_count"],
            )
            return dict(cached)

    allowed_input = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": allowed_input},
    ]
    retry_count = 0
    result = None
    for attempt in range(3):
        content = ""
        try:
            response = groq_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)
            result = validate_briefing(parsed, payload)
            retry_count = attempt
            source = "llm"
            break
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            retry_count = min(attempt + 1, 2)
            if attempt == 2:
                logger.warning("Manager briefing validation fell back after retries")
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The prior JSON failed validation. Return corrected JSON "
                        "using exactly the same input and rules."
                    ),
                }
            )
        except Exception:
            retry_count = attempt
            logger.warning("Manager briefing model call failed; using template fallback")
            break

    if result is None:
        ordered = sorted(
            actionable,
            key=lambda row: (
                -(_decimal(row.get("rs_impact")) or Decimal("0")),
                str(row.get("expires_at") or "9999"),
            ),
        )
        result = _template_briefing(ordered)
        source = "fallback"
    result = {**result, "source": source, "retry_count": retry_count}
    logger.info(
        "Manager briefing source=%s retries=%s cache=miss",
        source,
        retry_count,
    )
    with _CACHE_LOCK:
        _CACHE[cache_key] = dict(result)
    return result


def clear_briefing_cache() -> None:
    """Clear process-local briefing cache; intended for tests and refresh tooling."""
    with _CACHE_LOCK:
        _CACHE.clear()
