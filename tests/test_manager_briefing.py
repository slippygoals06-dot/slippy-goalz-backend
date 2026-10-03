import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.manager import briefing
from app.main import app
from app.routes import manager as manager_routes


def _snapshot():
    return {
        "as_of": "2026-10-03T18:00:00+05:00",
        "today": {
            "revenue": "10000",
            "bookings": 5,
            "completed": 4,
            "cancelled": 0,
            "no_shows": 1,
        },
        "baseline": {
            "revenue": "12000",
            "bookings": "6",
            "sample_size": 4,
            "revenue_sample_size": 4,
        },
        "vs_normal": {"revenue": "-16.67", "bookings": "-16.67"},
        "next_48h_empty_slots": {"count": 2},
        "unpaid_deposits": {"count": 1},
        "customers": [{"name": "Secret Customer", "message": "private text"}],
    }


def _finding(**overrides):
    finding = {
        "id": "finding-123",
        "type": "empty_slots_next_48h",
        "severity": "yellow",
        "confidence": "medium",
        "cause_known": False,
        "rs_impact": "1000",
        "evidence_json": {
            "empty_slot_count": 2,
            "deviation_pct": "-16.67",
            "customer_ref": "customer-44",
            "customer_name": "Secret Customer",
            "message": "private text",
        },
        "expires_at": "2026-10-04T10:00:00+05:00",
        "suggested_action": "Review and promote these open slots.",
        "title": "Contains no customer data",
    }
    finding.update(overrides)
    return finding


def _valid_output():
    return {
        "headline": "Two slots need review.",
        "cards": [
            {
                "finding_id": "finding-123",
                "plain_text": "Estimated value is Rs 1,000.",
                "why_it_matters": "Can't tell yet; no cause is established.",
                "next_step": "Review and promote these open slots.",
            }
        ],
        "leave_alone_note": "",
    }


class _MockGroq:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.contents.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content)
                )
            ]
        )


def _content(value):
    return value if isinstance(value, str) else json.dumps(value)


@pytest.fixture(autouse=True)
def clear_cache():
    briefing.clear_briefing_cache()
    yield
    briefing.clear_briefing_cache()


def test_valid_groq_briefing_uses_json_mode_and_safe_input():
    client = _MockGroq([_content(_valid_output())])

    result = briefing.generate_briefing(
        _snapshot(),
        [_finding()],
        client,
        "llama-3.3-70b-versatile",
    )

    assert result["source"] == "llm"
    assert result["retry_count"] == 0
    call = client.calls[0]
    assert call["temperature"] == 0
    assert call["response_format"] == {"type": "json_object"}
    submitted = call["messages"][1]["content"]
    assert "Secret Customer" not in submitted
    assert "private text" not in submitted
    assert "Rs 1,000" in submitted
    assert "2026-10-04 10:00 PKT" in submitted


def test_formatted_number_with_currency_and_commas_is_allowed():
    output = _valid_output()
    output["cards"][0]["plain_text"] = "Estimated value is Rs 1,000."
    payload = briefing.build_llm_input(_snapshot(), [_finding()])

    assert briefing.validate_briefing(output, payload) == output


def test_invented_number_is_rejected_and_retried():
    invalid = _valid_output()
    invalid["headline"] = "There are 99 urgent items."
    client = _MockGroq([_content(invalid), _content(_valid_output())])

    result = briefing.generate_briefing(
        _snapshot(),
        [_finding()],
        client,
        "llama-3.3-70b-versatile",
        refresh=True,
    )

    assert result["source"] == "llm"
    assert result["retry_count"] == 1
    assert len(client.calls) == 2


def test_unknown_finding_id_is_rejected():
    output = _valid_output()
    output["cards"][0]["finding_id"] = "finding-999"
    payload = briefing.build_llm_input(_snapshot(), [_finding()])

    with pytest.raises(ValueError, match="unknown finding_id"):
        briefing.validate_briefing(output, payload)


def test_causal_claim_without_known_cause_is_rejected():
    output = _valid_output()
    output["cards"][0]["why_it_matters"] = (
        "This happened because of an owner mistake. Can't tell yet."
    )
    payload = briefing.build_llm_input(_snapshot(), [_finding()])

    with pytest.raises(ValueError, match="Causal wording"):
        briefing.validate_briefing(output, payload)


def test_unknown_cause_requires_cant_tell_yet():
    output = _valid_output()
    output["cards"][0]["why_it_matters"] = "This needs attention."
    payload = briefing.build_llm_input(_snapshot(), [_finding()])

    with pytest.raises(ValueError, match="requires 'can't tell yet'"):
        briefing.validate_briefing(output, payload)


def test_malformed_json_retries_then_uses_template_fallback():
    client = _MockGroq(["not json", "{", "still not json"])

    result = briefing.generate_briefing(
        _snapshot(),
        [_finding()],
        client,
        "llama-3.3-70b-versatile",
        refresh=True,
    )

    assert result["source"] == "fallback"
    assert result["retry_count"] == 2
    assert len(client.calls) == 3
    assert result["cards"][0]["finding_id"] == "finding-123"
    assert "can't tell yet" in result["cards"][0]["why_it_matters"].lower()


def test_cache_is_keyed_by_input_and_manual_refresh_bypasses_it():
    client = _MockGroq([_content(_valid_output()), _content(_valid_output())])
    snapshot = _snapshot()
    findings = [_finding()]

    first = briefing.generate_briefing(
        snapshot, findings, client, "llama-3.3-70b-versatile"
    )
    cached = briefing.generate_briefing(
        snapshot, findings, client, "llama-3.3-70b-versatile"
    )
    refreshed = briefing.generate_briefing(
        snapshot,
        findings,
        client,
        "llama-3.3-70b-versatile",
        refresh=True,
    )

    assert first == cached
    assert refreshed["source"] == "llm"
    assert len(client.calls) == 2


def test_expired_finding_is_filtered_and_changes_payload_hash():
    finding = _finding(expires_at="2026-10-03T18:10:00+05:00")
    before_expiry = datetime.fromisoformat("2026-10-03T18:05:00+05:00")
    after_expiry = datetime.fromisoformat("2026-10-03T18:11:00+05:00")
    before_payload = briefing.build_llm_input(
        _snapshot(),
        briefing._unexpired_findings([finding], before_expiry),
    )
    active_after_expiry = briefing._unexpired_findings(
        [finding],
        after_expiry,
    )
    after_payload = briefing.build_llm_input(_snapshot(), active_after_expiry)

    assert len(before_payload["findings"]) == 1
    assert active_after_expiry == []
    assert after_payload["findings"] == []
    assert briefing._cache_key(before_payload, before_expiry) != (
        briefing._cache_key(after_payload, after_expiry)
    )


def test_expired_findings_only_return_leave_alone_without_groq():
    client = _MockGroq([])
    finding = _finding(expires_at="2026-10-03T17:59:59+05:00")

    result = briefing.generate_briefing(
        _snapshot(),
        [finding],
        client,
        "llama-3.3-70b-versatile",
    )

    assert result["cards"] == []
    assert "leave everything alone" in result["leave_alone_note"].lower()
    assert client.calls == []


def test_cache_hit_within_same_bucket_does_not_call_groq_again():
    client = _MockGroq([_content(_valid_output())])
    findings = [_finding()]
    first_snapshot = _snapshot()
    first_snapshot["as_of"] = "2026-10-03T18:05:00+05:00"
    second_snapshot = _snapshot()
    second_snapshot["as_of"] = "2026-10-03T18:25:00+05:00"

    first = briefing.generate_briefing(
        first_snapshot, findings, client, "llama-3.3-70b-versatile"
    )
    cached = briefing.generate_briefing(
        second_snapshot, findings, client, "llama-3.3-70b-versatile"
    )

    assert first == cached
    assert len(client.calls) == 1


def test_crossing_bucket_boundary_is_a_cache_miss():
    client = _MockGroq([_content(_valid_output()), _content(_valid_output())])
    findings = [_finding()]
    first_snapshot = _snapshot()
    first_snapshot["as_of"] = "2026-10-03T18:29:00+05:00"
    second_snapshot = _snapshot()
    second_snapshot["as_of"] = "2026-10-03T18:30:00+05:00"

    briefing.generate_briefing(
        first_snapshot, findings, client, "llama-3.3-70b-versatile"
    )
    briefing.generate_briefing(
        second_snapshot, findings, client, "llama-3.3-70b-versatile"
    )

    assert len(client.calls) == 2


def test_no_actionable_findings_skip_groq_and_return_leave_alone():
    client = _MockGroq([])
    row = _finding(rs_impact="0", suggested_action=None)

    result = briefing.generate_briefing(
        _snapshot(),
        [row],
        client,
        "llama-3.3-70b-versatile",
    )

    assert result["source"] == "fallback"
    assert result["cards"] == []
    assert "leave everything alone" in result["leave_alone_note"].lower()
    assert client.calls == []


def test_briefing_endpoint_requires_owner_authentication():
    response = TestClient(app).post(
        "/manager/briefing",
        json={"business_id": "00000000-0000-0000-0000-000000000001"},
    )

    assert response.status_code == 401


def test_briefing_response_includes_findings_for_dashboard_cards(monkeypatch):
    findings = [{"id": "finding-123", "severity": "yellow", "evidence_json": {}}]
    monkeypatch.setattr(
        manager_routes,
        "run_detectors",
        lambda _business_id: {
            "findings": findings,
            "business_state": {"Revenue": "NORMAL"},
            "snapshot": {"as_of": "2026-10-03T18:00:00+05:00"},
        },
    )
    monkeypatch.setattr(
        manager_routes,
        "generate_briefing",
        lambda *_args, **_kwargs: {
            "headline": "Review one item.",
            "cards": [],
            "leave_alone_note": "",
            "source": "fallback",
            "retry_count": 0,
        },
    )

    response = manager_routes.post_manager_briefing(
        manager_routes.ManagerBriefingRequest(
            business_id="00000000-0000-0000-0000-000000000001"
        ),
        _owner="owner",
    )

    assert response["findings"] == findings
    assert response["business_state"] == {"Revenue": "NORMAL"}
    assert response["headline"] == "Review one item."
