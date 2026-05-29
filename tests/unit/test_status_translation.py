"""
Unit tests for the status translation helpers and sanitiser functions
in api/routers/investigations.py.

All functions are pure — no DB fixtures needed.
"""
from __future__ import annotations

import pytest

# Import the private helpers we want to test.
# They live at module scope in the investigations router.
from api.routers.investigations import (
    _sanitise_case,
    _sanitise_detail,
    _to_db,
    _to_ui,
)


# ── _to_ui: DB status → frontend label ───────────────────────────────────────

class TestToUi:
    def test_in_progress_maps_to_investigating(self):
        assert _to_ui("in_progress") == "investigating"

    def test_pending_review_maps_to_triaged(self):
        assert _to_ui("pending_review") == "triaged"

    def test_dismissed_maps_to_closed(self):
        assert _to_ui("dismissed") == "closed"

    def test_on_hold_maps_to_open(self):
        assert _to_ui("on_hold") == "open"

    def test_passthrough_statuses_unchanged(self):
        for s in ("open", "escalated", "resolved", "closed"):
            assert _to_ui(s) == s

    def test_none_defaults_to_open(self):
        assert _to_ui(None) == "open"

    def test_empty_string_defaults_to_open(self):
        assert _to_ui("") == "open"

    def test_unknown_status_returned_as_is(self):
        # Unrecognised DB values should pass through rather than silently hide
        assert _to_ui("some_future_status") == "some_future_status"


# ── _to_db: frontend label → DB status ───────────────────────────────────────

class TestToDb:
    def test_investigating_maps_to_in_progress(self):
        assert _to_db("investigating") == "in_progress"

    def test_triaged_maps_to_pending_review(self):
        assert _to_db("triaged") == "pending_review"

    def test_closed_maps_to_dismissed(self):
        assert _to_db("closed") == "dismissed"

    def test_open_maps_to_on_hold(self):
        assert _to_db("open") == "on_hold"

    def test_passthrough_for_already_db_values(self):
        # DB-native values that have no UI alias should pass through
        for s in ("escalated", "resolved"):
            assert _to_db(s) == s

    def test_none_defaults_to_open(self):
        assert _to_db(None) == "open"

    def test_empty_defaults_to_open(self):
        assert _to_db("") == "open"

    def test_roundtrip_ui_to_db_to_ui(self):
        """A UI label converted to DB then back should equal the original."""
        for ui in ("investigating", "triaged"):
            assert _to_ui(_to_db(ui)) == ui

    def test_roundtrip_db_to_ui_to_db(self):
        """A DB value converted to UI then back should equal the original."""
        for db in ("in_progress", "pending_review"):
            assert _to_db(_to_ui(db)) == db


# ── _sanitise_case ────────────────────────────────────────────────────────────

class TestSanitiseCase:
    def _base(self) -> dict:
        return {
            "status":             "in_progress",
            "entity_name":        "Springfield CHC",
            "total_findings":     5,
            "critical_findings":  2,
            "high_findings":      3,
            "financial_exposure": 1500.0,
        }

    def test_status_is_translated(self):
        d = self._base()
        result = _sanitise_case(d)
        assert result["status"] == "investigating"

    def test_entity_name_preserved_when_set(self):
        d = self._base()
        result = _sanitise_case(d)
        assert result["entity_name"] == "Springfield CHC"

    def test_none_entity_name_becomes_unknown(self):
        d = self._base()
        d["entity_name"] = None
        result = _sanitise_case(d)
        assert result["entity_name"] == "Unknown Entity"

    def test_empty_entity_name_becomes_unknown(self):
        d = self._base()
        d["entity_name"] = ""
        result = _sanitise_case(d)
        assert result["entity_name"] == "Unknown Entity"

    def test_none_total_findings_becomes_zero(self):
        d = self._base()
        d["total_findings"] = None
        result = _sanitise_case(d)
        assert result["total_findings"] == 0

    def test_none_critical_findings_becomes_zero(self):
        d = self._base()
        d["critical_findings"] = None
        result = _sanitise_case(d)
        assert result["critical_findings"] == 0

    def test_none_high_findings_becomes_zero(self):
        d = self._base()
        d["high_findings"] = None
        result = _sanitise_case(d)
        assert result["high_findings"] == 0

    def test_none_financial_exposure_becomes_zero(self):
        d = self._base()
        d["financial_exposure"] = None
        result = _sanitise_case(d)
        assert result["financial_exposure"] == 0.0

    def test_string_numbers_coerced_to_int(self):
        d = self._base()
        d["total_findings"] = "7"
        result = _sanitise_case(d)
        assert result["total_findings"] == 7
        assert isinstance(result["total_findings"], int)

    def test_returns_same_dict_mutated(self):
        d = self._base()
        result = _sanitise_case(d)
        # Should return the same dict object (in-place mutation)
        assert result is d


# ── _sanitise_detail ──────────────────────────────────────────────────────────

class TestSanitiseDetail:
    def _base(self) -> dict:
        return {
            "status":             "pending_review",
            "entity_name":        "Tri-County Hospital",
            "total_findings":     10,
            "critical_findings":  3,
            "high_findings":      4,
            "financial_exposure": 8000.0,
            "medium_findings":    None,
            "low_findings":       None,
            "unique_patients":    None,
            "ndc_list":           None,
            "findings_by_rule":   None,
        }

    def test_inherits_case_sanitisation(self):
        d = self._base()
        result = _sanitise_detail(d)
        assert result["status"] == "triaged"         # pending_review → triaged
        assert result["entity_name"] == "Tri-County Hospital"

    def test_none_medium_findings_becomes_zero(self):
        d = self._base()
        result = _sanitise_detail(d)
        assert result["medium_findings"] == 0

    def test_none_low_findings_becomes_zero(self):
        d = self._base()
        result = _sanitise_detail(d)
        assert result["low_findings"] == 0

    def test_none_unique_patients_becomes_zero(self):
        d = self._base()
        result = _sanitise_detail(d)
        assert result["unique_patients"] == 0

    def test_none_ndc_list_becomes_empty_list(self):
        d = self._base()
        result = _sanitise_detail(d)
        assert result["ndc_list"] == []

    def test_none_findings_by_rule_becomes_empty_dict(self):
        d = self._base()
        result = _sanitise_detail(d)
        assert result["findings_by_rule"] == {}

    def test_populated_ndc_list_preserved(self):
        d = self._base()
        d["ndc_list"] = ["00069420030", "00006001754"]
        result = _sanitise_detail(d)
        assert result["ndc_list"] == ["00069420030", "00006001754"]

    def test_populated_findings_by_rule_preserved(self):
        d = self._base()
        d["findings_by_rule"] = {"DD-001": 5, "MO-002": 3}
        result = _sanitise_detail(d)
        assert result["findings_by_rule"] == {"DD-001": 5, "MO-002": 3}

    def test_all_none_detail_fields_safely_sanitised(self):
        """Worst-case: every nullable field from DB lateral join is NULL."""
        d = {
            "status":             None,
            "entity_name":        None,
            "total_findings":     None,
            "critical_findings":  None,
            "high_findings":      None,
            "financial_exposure": None,
            "medium_findings":    None,
            "low_findings":       None,
            "unique_patients":    None,
            "ndc_list":           None,
            "findings_by_rule":   None,
        }
        result = _sanitise_detail(d)
        assert result["status"] == "open"
        assert result["entity_name"] == "Unknown Entity"
        assert result["total_findings"] == 0
        assert result["medium_findings"] == 0
        assert result["ndc_list"] == []
        assert result["findings_by_rule"] == {}
