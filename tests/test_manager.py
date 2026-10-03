from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.manager.run as manager_run
from app.manager import SINGLETON_BUSINESS_ID
from app.manager.detectors.abandoned_bookings import detect_abandoned_bookings
from app.manager.detectors.empty_slots_next_48h import (
    detect_empty_slots_next_48h,
)
from app.manager.detectors.no_show_rate import detect_no_show_rate
from app.manager.detectors.revenue_vs_weekday_avg import (
    detect_revenue_vs_weekday_avg,
)
from app.manager.detectors.unpaid_deposits import detect_unpaid_deposits
from app.manager.detectors.abandoned_bookings import detect_abandoned_bookings
from app.manager.models import Finding
from app.manager.snapshot import _snapshot_from_rows


def _baseline_snapshot(
    *,
    today_revenue=Decimal("10000"),
    today_bookings=10,
    today_completed=9,
    today_no_shows=1,
    baseline_revenue=Decimal("10000"),
    baseline_bookings=Decimal("10"),
    baseline_completed=Decimal("9"),
    baseline_no_shows=Decimal("1"),
    sample_size=4,
):
    return {
        "as_of": "2026-10-03T18:00:00+05:00",
        "today": {
            "revenue": today_revenue,
            "bookings": today_bookings,
            "completed": today_completed,
            "cancelled": 0,
            "no_shows": today_no_shows,
        },
        "baseline": {
            "revenue": baseline_revenue,
            "bookings": baseline_bookings,
            "completed": baseline_completed,
            "no_shows": baseline_no_shows,
            "sample_size": sample_size,
            "revenue_sample_size": sample_size,
        },
        "vs_normal": {"revenue": Decimal("0"), "bookings": Decimal("0")},
        "funnel": {"chat_started": None, "booking_started": None, "paid": None},
        "abandoned_bookings": [],
        "booking_events": [],
        "estimated_average_booking_value": Decimal("3000"),
        "unpaid_deposits": {"count": 0, "items": []},
        "overdue_jobs": {"count": None, "items": []},
        "next_48h_empty_slots": {"count": 0, "slots": []},
        "tracking": {
            "abandoned_bookings": True,
            "unpaid_deposits": True,
            "empty_slots": True,
        },
    }


def test_snapshot_uses_karachi_day_and_four_same_weekday_samples():
    as_of = datetime.fromisoformat("2026-10-03T10:00:00+05:00")
    bookings = [
        {"Date": "2026-10-03", "Time": "09:00", "Status": "Completed", "amount": "3000"},
        {"Date": "2026-10-03", "Time": "10:00", "Status": "No-show", "amount": None},
        {"Date": "2026-09-26", "Time": "09:00", "Status": "Completed", "amount": "2000"},
        {"Date": "2026-09-12", "Time": "09:00", "Status": "Completed", "amount": "1000"},
    ]
    slots = [
        {"id": "s1", "Date": "2026-09-19", "Time": "11:00", "Status": "Available"},
        {"id": "s2", "Date": "2026-09-26", "Time": "12:00", "Status": "Booked"},
        {"id": "s3", "Date": "2026-09-12", "Time": "12:00", "Status": "Available"},
        {"id": "s4", "Date": "2026-10-03", "Time": "12:00", "Status": "Available"},
        {"id": "s5", "Date": "2026-10-05", "Time": "10:01", "Status": "Available"},
    ]

    snapshot = _snapshot_from_rows(
        SINGLETON_BUSINESS_ID,
        as_of,
        bookings,
        slots,
    )

    assert snapshot["today"]["revenue"] == Decimal("3000")
    assert snapshot["today"]["bookings"] == 2
    assert snapshot["today"]["completed"] == 1
    assert snapshot["today"]["no_shows"] == 1
    assert snapshot["baseline"]["sample_size"] == 3
    assert snapshot["baseline"]["revenue"] == Decimal("1000")
    assert snapshot["next_48h_empty_slots"]["count"] == 1
    assert snapshot["next_48h_empty_slots"]["slots"][0]["potential_rs"] == Decimal("1500")
    assert snapshot["next_48h_empty_slots"]["slots"][0]["estimated"] is True
    assert snapshot["next_48h_empty_slots"]["slots"][0]["value_basis"] == "same_weekday_4_week_average"
    assert snapshot["tracking"]["empty_slot_potential_value"] is True


def test_normal_day_has_no_findings():
    snapshot = _baseline_snapshot()
    assert detect_abandoned_bookings(snapshot) == []
    assert detect_empty_slots_next_48h(snapshot) == []
    assert detect_no_show_rate(snapshot) == []
    assert detect_revenue_vs_weekday_avg(snapshot) == []
    assert detect_abandoned_bookings(snapshot) == []
    assert detect_unpaid_deposits(snapshot) == []


def test_empty_slot_estimate_falls_back_to_overall_average():
    as_of = datetime.fromisoformat("2026-10-03T10:00:00+05:00")
    bookings = [
        {"Date": "2026-09-28", "Status": "Completed", "amount": "1000"},
        {"Date": "2026-10-01", "Status": "Completed", "amount": "3000"},
    ]
    slots = [
        {"id": "s1", "Date": "2026-10-03", "Time": "12:00", "Status": "Available"},
    ]

    snapshot = _snapshot_from_rows(
        SINGLETON_BUSINESS_ID,
        as_of,
        bookings,
        slots,
    )

    slot, = snapshot["next_48h_empty_slots"]["slots"]
    assert slot["potential_rs"] == Decimal("2000")
    assert slot["estimated"] is True
    assert slot["value_basis"] == "overall_4_week_average"


def test_empty_slot_finding_marks_estimates_in_evidence():
    snapshot = _baseline_snapshot()
    snapshot["next_48h_empty_slots"] = {
        "count": 2,
        "slots": [
            {
                "date": "2026-10-04",
                "time": "09:00",
                "potential_rs": Decimal("3000"),
                "estimated": True,
                "value_basis": "same_weekday_4_week_average",
            },
            {
                "date": "2026-10-03",
                "time": "20:00",
                "potential_rs": Decimal("2500"),
                "estimated": True,
                "value_basis": "overall_4_week_average",
            },
        ],
    }

    finding, = detect_empty_slots_next_48h(snapshot)

    assert finding.rs_impact == Decimal("5500")
    assert finding.evidence["potential_rs_total"] == Decimal("5500")
    assert finding.evidence["estimated"] is True
    assert all(slot["estimated"] for slot in finding.evidence["slots"])


def test_empty_slots_finding_includes_evidence_and_expiry():
    snapshot = _baseline_snapshot()
    snapshot["next_48h_empty_slots"] = {
        "count": 2,
        "slots": [
            {
                "date": "2026-10-04",
                "time": "09:00",
                "potential_rs": None,
                "estimated": False,
            },
            {
                "date": "2026-10-03",
                "time": "20:00",
                "potential_rs": None,
                "estimated": False,
            },
        ],
    }

    finding, = detect_empty_slots_next_48h(snapshot)

    assert finding.evidence["empty_slot_count"] == 2
    assert finding.rs_impact == Decimal("0")
    assert finding.suggested_action
    assert finding.confidence == "low"
    assert finding.expires_at == datetime.fromisoformat("2026-10-03T20:00:00+05:00")


def test_abandoned_booking_requires_two_hours_without_submission():
    snapshot = _baseline_snapshot()
    snapshot["booking_events"] = [
        {
            "id": "start-1",
            "booking_ref": None,
            "customer_ref": "+923001234567",
            "event_type": "booking_started",
            "created_at": "2026-10-03T15:00:00+05:00",
        },
    ]

    finding, = detect_abandoned_bookings(snapshot)

    assert finding.rs_impact == Decimal("3000")
    assert finding.evidence["estimated"] is True
    assert finding.evidence["age_minutes"] == 180
    assert finding.dedupe_key == "abandoned_booking:start-1"


def test_abandoned_booking_is_not_flagged_when_submitted_within_two_hours():
    snapshot = _baseline_snapshot()
    snapshot["booking_events"] = [
        {
            "id": "start-1",
            "customer_ref": "+923001234567",
            "event_type": "booking_started",
            "created_at": "2026-10-03T15:00:00+05:00",
        },
        {
            "id": "confirm-1",
            "booking_ref": "CUST-123",
            "customer_ref": "+923001234567",
            "event_type": "booking_confirmed",
            "created_at": "2026-10-03T16:30:00+05:00",
        },
    ]

    assert detect_abandoned_bookings(snapshot) == []


def test_abandoned_booking_finding_resolves_when_attempt_is_later_submitted(
    monkeypatch,
):
    snapshot = _baseline_snapshot()
    snapshot["as_of"] = "2026-10-03T22:00:00+05:00"
    snapshot["booking_events"] = [
        {
            "id": "start-1",
            "customer_ref": "+923001234567",
            "event_type": "booking_started",
            "created_at": "2026-10-03T15:00:00+05:00",
        },
        {
            "id": "confirm-1",
            "booking_ref": "CUST-123",
            "customer_ref": "+923001234567",
            "event_type": "booking_confirmed",
            "created_at": "2026-10-03T20:00:00+05:00",
        },
    ]
    client = _FindingsClient()
    client.rows.append(
        {
            "id": "abandoned-row",
            "business_id": SINGLETON_BUSINESS_ID,
            "type": "abandoned_bookings",
            "dedupe_key": "abandoned_booking:start-1",
            "status": "open",
            "rs_impact": "3000",
            "expires_at": None,
        }
    )
    monkeypatch.setattr(manager_run, "supabase", client)
    monkeypatch.setattr(manager_run, "build_snapshot", lambda _business_id: snapshot)

    result = manager_run.run_detectors(SINGLETON_BUSINESS_ID)

    assert result["findings"] == []
    assert client.rows[0]["status"] == "done"
    assert client.rows[0]["verified_result"]["confirmed_at"] == (
        "2026-10-03T20:00:00+05:00"
    )


def test_abandoned_booking_without_history_has_no_invented_money_value():
    snapshot = _baseline_snapshot()
    snapshot["estimated_average_booking_value"] = None
    snapshot["booking_events"] = [
        {
            "id": "start-1",
            "customer_ref": "+923001234567",
            "event_type": "booking_started",
            "created_at": "2026-10-03T15:00:00+05:00",
        },
    ]

    finding, = detect_abandoned_bookings(snapshot)

    assert finding.rs_impact == Decimal("0")
    assert finding.evidence["estimated_booking_value_rs"] is None
    assert finding.evidence["estimated"] is False
    assert finding.suggested_action


def test_unpaid_deposits_only_flags_required_unconfirmed_advances():
    snapshot = _baseline_snapshot()
    snapshot["unpaid_deposits"] = {
        "count": 2,
        "items": [
            {
                "booking_ref": "B-1",
                "customer_ref": "customer-1",
                "date": "2026-10-04",
                "time": "12:00",
                "deposit_amount": Decimal("1000"),
                "deposit_paid": False,
            },
            {
                "booking_ref": "B-2",
                "customer_ref": "customer-2",
                "date": "2026-10-04",
                "time": "14:00",
                "deposit_amount": Decimal("2000"),
                "deposit_paid": True,
            },
            {
                "booking_ref": "B-3",
                "customer_ref": "customer-3",
                "date": "2026-10-04",
                "time": "15:00",
                "deposit_amount": None,
                "deposit_paid": False,
            },
        ],
    }

    finding, = detect_unpaid_deposits(snapshot)

    assert finding.rs_impact == Decimal("1000")
    assert finding.evidence["booking_ref"] == "B-1"
    assert finding.dedupe_key == "unpaid_deposit:B-1"


def test_unpaid_deposit_finding_resolves_when_no_longer_due(monkeypatch):
    snapshot = _baseline_snapshot()
    client = _FindingsClient()
    client.rows.append(
        {
            "id": "deposit-row",
            "business_id": SINGLETON_BUSINESS_ID,
            "type": "unpaid_deposits",
            "dedupe_key": "unpaid_deposit:B-1",
            "status": "open",
            "rs_impact": "1000",
            "expires_at": None,
        }
    )
    monkeypatch.setattr(manager_run, "supabase", client)
    monkeypatch.setattr(manager_run, "build_snapshot", lambda _business_id: snapshot)

    result = manager_run.run_detectors(SINGLETON_BUSINESS_ID)

    assert result["findings"] == []
    assert client.rows[0]["status"] == "done"
    assert "no longer outstanding" in client.rows[0]["verified_result"]["result"]


def test_snapshot_lists_unpaid_deposits_only_for_upcoming_required_advances():
    as_of = datetime.fromisoformat("2026-10-03T10:00:00+05:00")
    bookings = [
        {
            "Date": "2026-10-04",
            "Time": "12:00",
            "Status": "Pending",
            "Booking ID": "B-1",
            "Phone": "+923001234567",
            "deposit_amount": "1500",
            "deposit_paid": False,
        },
        {
            "Date": "2026-10-04",
            "Time": "13:00",
            "Status": "Confirmed",
            "Booking ID": "B-2",
            "deposit_amount": "2000",
            "deposit_paid": True,
        },
        {
            "Date": "2026-10-04",
            "Time": "14:00",
            "Status": "Confirmed",
            "Booking ID": "B-3",
            "deposit_amount": None,
            "deposit_paid": False,
        },
    ]

    snapshot = _snapshot_from_rows(
        SINGLETON_BUSINESS_ID,
        as_of,
        bookings,
        [],
    )

    assert snapshot["unpaid_deposits"]["count"] == 1
    assert snapshot["unpaid_deposits"]["items"][0]["booking_ref"] == "B-1"


def test_no_show_spike_uses_relative_threshold_and_baseline_impact():
    snapshot = _baseline_snapshot(
        today_completed=8,
        today_no_shows=2,
    )

    finding, = detect_no_show_rate(snapshot)

    assert finding.evidence["relative_deviation_pct"] == Decimal("100")
    assert finding.rs_impact == Decimal("2222")
    assert finding.confidence == "high"
    assert finding.severity == "red"


def test_revenue_drop_over_25_percent_is_flagged():
    snapshot = _baseline_snapshot(today_revenue=Decimal("7000"))
    snapshot["vs_normal"]["revenue"] = Decimal("-30")

    finding, = detect_revenue_vs_weekday_avg(snapshot)

    assert finding.evidence["deviation_pct"] == Decimal("-30")
    assert finding.rs_impact == Decimal("3000")
    assert finding.confidence == "medium"
    assert finding.severity == "yellow"


def test_new_business_with_less_than_two_weeks_does_not_crash_or_overclaim():
    snapshot = _baseline_snapshot(
        today_revenue=Decimal("0"),
        today_completed=0,
        today_no_shows=0,
        baseline_revenue=Decimal("5000"),
        sample_size=1,
    )

    assert detect_no_show_rate(snapshot) == []
    assert detect_revenue_vs_weekday_avg(snapshot) == []


def test_zero_impact_finding_without_action_is_dropped():
    finding = Finding(
        type="test",
        severity="yellow",
        title="No actionable evidence",
        evidence={},
        confidence="low",
        rs_impact=Decimal("0"),
        suggested_action=" ",
    )

    assert manager_run._eligible_findings([finding]) == []
    assert manager_run._eligible_findings(
        [
            Finding(
                type="test",
                severity="yellow",
                title="Actionable",
                evidence={},
                confidence="low",
                rs_impact=Decimal("0"),
                suggested_action="Review this finding",
            )
        ]
    )


class _FindingsTable:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}
        self.selected_fields = None

    def select(self, fields):
        self.operation = "select"
        self.selected_fields = fields
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def range(self, _start, _end):
        return self

    def limit(self, _limit):
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def execute(self):
        matching = [
            row for row in self.rows
            if all(row.get(key) == value for key, value in self.filters.items())
        ]
        if self.operation == "select":
            rows = (
                [{"id": row["id"]} for row in matching]
                if self.selected_fields == "id"
                else matching
            )
            return SimpleNamespace(data=rows)
        if self.operation == "update":
            for row in matching:
                row.update(self.payload)
            return SimpleNamespace(data=matching)
        raise AssertionError(f"Unexpected operation: {self.operation}")


class _UpsertFinding:
    def __init__(self, rows, payload):
        self.rows = rows
        self.payload = payload

    def execute(self):
        row = next(
            (
                row
                for row in self.rows
                if row["business_id"] == self.payload["p_business_id"]
                and row["dedupe_key"] == self.payload["p_dedupe_key"]
                and row["status"] == "open"
            ),
            None,
        )
        fields = {
            "business_id": "p_business_id",
            "type": "p_type",
            "severity": "p_severity",
            "title": "p_title",
            "evidence_json": "p_evidence_json",
            "confidence": "p_confidence",
            "cause_known": "p_cause_known",
            "rs_impact": "p_rs_impact",
            "expires_at": "p_expires_at",
            "suggested_action": "p_suggested_action",
            "dedupe_key": "p_dedupe_key",
        }
        if row is None:
            row = {
                key: self.payload[value]
                for key, value in fields.items()
            }
            row.update(status="open", id=f"finding-{len(self.rows) + 1}")
            self.rows.append(row)
        else:
            for key, value in fields.items():
                if key not in {"business_id", "dedupe_key"}:
                    row[key] = self.payload[value]
        return SimpleNamespace(data=[row])


class _FindingsClient:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "findings"
        return _FindingsTable(self.rows)

    def rpc(self, name, payload):
        assert name == "manager_upsert_open_finding"
        return _UpsertFinding(self.rows, payload)


def test_rerunning_detectors_updates_open_findings_without_duplicates(monkeypatch):
    snapshot = _baseline_snapshot(today_revenue=Decimal("0"))
    snapshot["vs_normal"]["revenue"] = Decimal("-100")
    snapshot["next_48h_empty_slots"] = {
        "count": 1,
        "slots": [{"date": "2026-10-03", "time": "20:00", "potential_rs": "2000"}],
    }
    client = _FindingsClient()
    client.rows.append(
        {
            "id": "existing-open",
            "business_id": SINGLETON_BUSINESS_ID,
            "dedupe_key": "older-finding",
            "status": "open",
            "rs_impact": "0",
            "expires_at": None,
        }
    )
    monkeypatch.setattr(manager_run, "supabase", client)
    monkeypatch.setattr(manager_run, "build_snapshot", lambda _business_id: snapshot)

    first = manager_run.run_detectors(SINGLETON_BUSINESS_ID)
    second = manager_run.run_detectors(SINGLETON_BUSINESS_ID)

    assert len(client.rows) == 3
    assert [row["id"] for row in first["findings"]] == [
        row["id"] for row in second["findings"]
    ]
    assert [row["rs_impact"] for row in second["findings"]] == [
        "10000",
        "2000",
        "0",
    ]
    assert "existing-open" in [row["id"] for row in second["findings"]]
    assert second["business_state_tracking"]["Payments"] is True
    assert second["business_state_tracking"]["Customers"] is False
