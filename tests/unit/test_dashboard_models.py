"""
Unit tests for dashboard and entities Pydantic response models.

Validates field types, Optional handling, and default values without
touching the database.
"""
from __future__ import annotations

import pytest

from api.routers.dashboard import (
    DashboardSummary,
    ExposureTrendPoint,
    RiskMatrixCell,
    RuleBreakdownItem,
)
from api.routers.entities import (
    CoveredEntity,
    EntityListResponse,
    EntitySummary,
)

# ── DashboardSummary ──────────────────────────────────────────────────────────

class TestDashboardSummary:
    def test_all_zeros(self):
        s = DashboardSummary(
            open_cases=0, escalated_cases=0, triaged_cases=0,
            investigating_cases=0, total_findings=0, critical_findings=0,
            high_findings=0, total_exposure=None, avg_risk_score=None,
            covered_entities=0, uploads_this_week=0, findings_this_week=0,
        )
        assert s.open_cases == 0
        assert s.total_exposure is None

    def test_with_exposure(self):
        s = DashboardSummary(
            open_cases=5, escalated_cases=1, triaged_cases=2,
            investigating_cases=2, total_findings=120, critical_findings=3,
            high_findings=17, total_exposure=48500.0, avg_risk_score=0.72,
            covered_entities=14, uploads_this_week=3, findings_this_week=22,
        )
        assert s.total_exposure == 48500.0
        assert s.avg_risk_score == pytest.approx(0.72)

    def test_optional_fields_accept_none(self):
        s = DashboardSummary(
            open_cases=0, escalated_cases=0, triaged_cases=0,
            investigating_cases=0, total_findings=0, critical_findings=0,
            high_findings=0, total_exposure=None, avg_risk_score=None,
            covered_entities=0, uploads_this_week=0, findings_this_week=0,
        )
        assert s.total_exposure is None
        assert s.avg_risk_score is None


class TestRuleBreakdownItem:
    def test_basic(self):
        r = RuleBreakdownItem(
            rule_code="DD-001",
            rule_name="Duplicate Discount",
            severity="critical",
            count=12,
            exposure=9800.0,
        )
        assert r.rule_code == "DD-001"
        assert r.count == 12

    def test_no_exposure(self):
        r = RuleBreakdownItem(
            rule_code="MO-002",
            rule_name="Medicaid Overlap",
            severity="high",
            count=5,
            exposure=None,
        )
        assert r.exposure is None


class TestRiskMatrixCell:
    def test_valid_cell(self):
        c = RiskMatrixCell(status="open", priority="critical", count=7)
        assert c.count == 7

    def test_zero_count(self):
        c = RiskMatrixCell(status="escalated", priority="medium", count=0)
        assert c.count == 0


class TestExposureTrendPoint:
    def test_basic(self):
        p = ExposureTrendPoint(date="2025-01-15", exposure=12345.67, count=8)
        assert p.date == "2025-01-15"
        assert p.exposure == pytest.approx(12345.67)
        assert p.count == 8

    def test_zero_exposure(self):
        p = ExposureTrendPoint(date="2025-01-01", exposure=0.0, count=0)
        assert p.exposure == 0.0


# ── Entities models ───────────────────────────────────────────────────────────

class TestCoveredEntity:
    def _make(self, **kwargs) -> CoveredEntity:
        defaults = {
            "ce_id": "550e8400-e29b-41d4-a716-446655440000",
            "hrsa_id": "340B123",
            "entity_name": "Test Community Health Center",
            "entity_type_code": "CHC",
            "entity_type_description": "Community Health Center (FQHC)",
            "city": "Springfield",
            "state_code": "IL",
            "zip_code": "62701",
            "npi": "1234567890",
            "primary_340b_program": "Community Health Centers",
            "program_status": "Active",
            "program_participation_start": "2010-01-01",
            "is_active": True,
        }
        defaults.update(kwargs)
        return CoveredEntity(**defaults)

    def test_full_entity(self):
        e = self._make()
        assert e.entity_name == "Test Community Health Center"
        assert e.is_active is True

    def test_optional_fields_can_be_none(self):
        e = self._make(
            entity_type_code=None,
            entity_type_description=None,
            city=None,
            state_code=None,
            zip_code=None,
            npi=None,
            primary_340b_program=None,
            program_participation_start=None,
        )
        assert e.city is None
        assert e.npi is None

    def test_inactive_entity(self):
        e = self._make(program_status="Terminated", is_active=False)
        assert e.is_active is False
        assert e.program_status == "Terminated"


class TestEntitySummary:
    def test_with_data(self):
        s = EntitySummary(
            ce_id="550e8400-e29b-41d4-a716-446655440000",
            entity_name="Test CHC",
            open_cases=3,
            total_findings=45,
            critical_findings=2,
            total_exposure=7800.0,
            avg_risk_score=0.63,
        )
        assert s.open_cases == 3
        assert s.total_exposure == pytest.approx(7800.0)

    def test_no_findings(self):
        s = EntitySummary(
            ce_id="550e8400-e29b-41d4-a716-446655440000",
            entity_name="New CHC",
            open_cases=0,
            total_findings=0,
            critical_findings=0,
            total_exposure=None,
            avg_risk_score=None,
        )
        assert s.total_findings == 0
        assert s.total_exposure is None


class TestEntityListResponse:
    def test_empty_list(self):
        r = EntityListResponse(entities=[], total=0, page=1, limit=25)
        assert r.total == 0
        assert r.entities == []

    def test_pagination_metadata(self):
        r = EntityListResponse(entities=[], total=100, page=3, limit=25)
        assert r.page == 3
        assert r.limit == 25
