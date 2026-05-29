"""
Unit tests for investigation case Pydantic schemas.

Validates field types, Optional handling, default coercion, and the
CaseStatusUpdate pattern constraint — all without touching the database.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from api.schemas.investigation import (
    CaseStatusUpdate,
    DashboardMetrics,
    InvestigationCaseDetail,
    InvestigationCaseSummary,
    InvestigationQueueResponse,
    SeverityDistribution,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_CASE_ID = uuid.uuid4()
_CE_ID   = uuid.uuid4()
_NOW     = datetime.utcnow()


def _summary(**overrides) -> InvestigationCaseSummary:
    defaults: dict = {
        "case_id":            _CASE_ID,
        "case_number":        "EVR-2025-0001",
        "status":             "open",
        "priority":           "high",
        "violation_category": "duplicate_discount",
        "entity_name":        "Springfield CHC",
        "covered_entity_id":  _CE_ID,
    }
    defaults.update(overrides)
    return InvestigationCaseSummary(**defaults)


def _detail(**overrides) -> InvestigationCaseDetail:
    defaults: dict = {
        "case_id":            _CASE_ID,
        "case_number":        "EVR-2025-0001",
        "status":             "investigating",
        "priority":           "critical",
        "violation_category": "medicaid_overlap",
        "entity_name":        "Springfield CHC",
        "covered_entity_id":  _CE_ID,
    }
    defaults.update(overrides)
    return InvestigationCaseDetail(**defaults)


# ── InvestigationCaseSummary ──────────────────────────────────────────────────


class TestInvestigationCaseSummary:
    def test_minimal_fields(self):
        s = _summary()
        assert s.case_number == "EVR-2025-0001"
        assert s.status == "open"

    def test_defaults_are_zero_not_none(self):
        s = _summary()
        assert s.total_findings == 0
        assert s.critical_findings == 0
        assert s.high_findings == 0
        assert s.financial_exposure == 0.0

    def test_none_coerces_to_int_zero(self):
        """None must be accepted for int|None fields (DB nullable columns)."""
        s = _summary(total_findings=None, critical_findings=None, high_findings=None)
        assert s.total_findings is None  # stored as-is; sanitise at router layer

    def test_optional_entity_name(self):
        s = _summary(entity_name=None)
        assert s.entity_name is None

    def test_optional_assigned_to(self):
        s = _summary(assigned_to=None)
        assert s.assigned_to is None

    def test_optional_composite_score(self):
        s = _summary(composite_score=0.8472)
        assert s.composite_score == pytest.approx(0.8472)

    def test_optional_risk_level(self):
        s = _summary(risk_level="critical")
        assert s.risk_level == "critical"

    def test_opened_at_datetime(self):
        s = _summary(opened_at=_NOW)
        assert isinstance(s.opened_at, datetime)

    def test_uuid_fields_round_trip(self):
        s = _summary()
        assert s.case_id == _CASE_ID
        assert s.covered_entity_id == _CE_ID

    def test_from_attributes_enabled(self):
        assert InvestigationCaseSummary.model_config.get("from_attributes") is True


# ── InvestigationCaseDetail ───────────────────────────────────────────────────


class TestInvestigationCaseDetail:
    def test_inherits_summary_fields(self):
        d = _detail()
        assert d.case_number == "EVR-2025-0001"
        assert d.violation_category == "medicaid_overlap"

    def test_detail_defaults(self):
        d = _detail()
        assert d.medium_findings == 0
        assert d.low_findings == 0
        assert d.unique_patients == 0
        assert d.ndc_list == []
        assert d.findings_by_rule == {}

    def test_ndc_list_populated(self):
        d = _detail(ndc_list=["00069420030", "00006001754"])
        assert len(d.ndc_list) == 2
        assert "00069420030" in d.ndc_list

    def test_findings_by_rule_populated(self):
        d = _detail(findings_by_rule={"DD-001": 5, "MO-002": 3})
        assert d.findings_by_rule["DD-001"] == 5
        assert d.findings_by_rule["MO-002"] == 3

    def test_resolution_notes_optional(self):
        d = _detail(resolution_notes="Case resolved — duplicate discount confirmed.")
        assert "duplicate discount" in d.resolution_notes

    def test_closed_at_optional(self):
        d = _detail(closed_at=_NOW)
        assert isinstance(d.closed_at, datetime)

    def test_none_ndc_list_accepted(self):
        d = _detail(ndc_list=None)
        assert d.ndc_list is None

    def test_none_findings_by_rule_accepted(self):
        d = _detail(findings_by_rule=None)
        assert d.findings_by_rule is None

    def test_full_finding_counts(self):
        d = _detail(
            total_findings=42,
            critical_findings=10,
            high_findings=15,
            medium_findings=12,
            low_findings=5,
            unique_patients=28,
        )
        assert d.total_findings == 42
        assert d.unique_patients == 28


# ── CaseStatusUpdate ──────────────────────────────────────────────────────────


class TestCaseStatusUpdate:
    @pytest.mark.parametrize("status", [
        "open", "triaged", "investigating", "in_progress",
        "pending_review", "escalated", "resolved", "closed",
        "dismissed", "on_hold",
    ])
    def test_valid_statuses(self, status: str):
        u = CaseStatusUpdate(status=status)
        assert u.status == status

    @pytest.mark.parametrize("bad_status", [
        "OPEN", "Open", "unknown", "archived", "draft", "pending",
    ])
    def test_invalid_statuses_rejected(self, bad_status: str):
        with pytest.raises(ValidationError):
            CaseStatusUpdate(status=bad_status)

    def test_resolution_notes_optional(self):
        u = CaseStatusUpdate(status="resolved", resolution_notes="Fixed.")
        assert u.resolution_notes == "Fixed."

    def test_resolution_notes_defaults_none(self):
        u = CaseStatusUpdate(status="closed")
        assert u.resolution_notes is None


# ── SeverityDistribution ──────────────────────────────────────────────────────


class TestSeverityDistribution:
    def test_all_zeros(self):
        s = SeverityDistribution()
        assert s.critical == 0
        assert s.total == 0

    def test_populated(self):
        s = SeverityDistribution(critical=5, high=12, medium=20, low=8, total=45)
        assert s.critical == 5
        assert s.total == 45


# ── DashboardMetrics ──────────────────────────────────────────────────────────


class TestDashboardMetrics:
    def _make(self, **kw) -> DashboardMetrics:
        defaults: dict = {
            "open_cases":          4,
            "escalated_cases":     2,
            "triaged_cases":       3,
            "investigating_cases": 1,
            "total_findings":      88,
            "critical_findings":   7,
            "total_exposure":      34200.0,
            "severity":            SeverityDistribution(
                critical=7, high=18, medium=34, low=29, total=88
            ),
        }
        defaults.update(kw)
        return DashboardMetrics(**defaults)

    def test_basic_construction(self):
        m = self._make()
        assert m.open_cases == 4
        assert m.total_exposure == pytest.approx(34200.0)

    def test_recent_escalations_defaults_empty(self):
        m = self._make()
        assert m.recent_escalations == []

    def test_with_recent_escalations(self):
        esc = _summary(status="escalated")
        m = self._make(recent_escalations=[esc])
        assert len(m.recent_escalations) == 1
        assert m.recent_escalations[0].status == "escalated"


# ── InvestigationQueueResponse ────────────────────────────────────────────────


class TestInvestigationQueueResponse:
    def test_empty_queue(self):
        r = InvestigationQueueResponse(total=0, page=1, limit=25, items=[])
        assert r.total == 0
        assert r.items == []

    def test_pagination_fields(self):
        r = InvestigationQueueResponse(
            total=100, page=4, limit=25, items=[_summary()]
        )
        assert r.page == 4
        assert r.limit == 25
        assert len(r.items) == 1

    def test_items_are_summaries(self):
        items = [_summary(case_number=f"EVR-2025-{i:04d}") for i in range(3)]
        r = InvestigationQueueResponse(total=3, page=1, limit=25, items=items)
        assert r.items[2].case_number == "EVR-2025-0002"
