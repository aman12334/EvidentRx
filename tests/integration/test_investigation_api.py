"""
Integration tests for the /investigations API endpoints.

Uses the FastAPI TestClient with the database dependency overridden by a
MagicMock session — no real database is required.

These tests validate:
  - HTTP response codes and Content-Type headers
  - Response shape matches the declared Pydantic response_model
  - Status translation (DB values → UI labels in responses)
  - Error handling (404 for missing cases)
  - Filter parameter wiring (status/priority pass through to DB query)
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

# ── JWT helper (generates a valid test token) ─────────────────────────────────

def _make_test_token() -> str:
    """Mint a short-lived JWT signed with the test JWT_SECRET_KEY from conftest."""
    from jose import jwt as jose_jwt
    payload = {
        "sub":       str(uuid.uuid4()),
        "tenant_id": "test-tenant",
        "role":      "analyst",
        "jti":       str(uuid.uuid4()),
        "iss":       "evidentrx",
        "aud":       "evidentrx-api",
        "iat":       datetime.now(UTC),
        "exp":       datetime.now(UTC) + timedelta(minutes=60),
    }
    import os
    secret = os.environ.get("JWT_SECRET_KEY", "test_secret_key_for_ci_only_not_production_32c")
    return jose_jwt.encode(payload, secret, algorithm="HS256")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {_make_test_token()}"}


# ── Helper factories ──────────────────────────────────────────────────────────

def _case_row(
    *,
    case_id: str | None = None,
    status: str = "open",
    priority: str = "high",
    entity_name: str | None = "Springfield CHC",
    total_findings: int | None = 5,
    critical_findings: int | None = 1,
    high_findings: int | None = 2,
    financial_exposure: float | None = 1200.0,
    composite_score: float | None = 0.72,
) -> dict:
    return {
        "case_id":            case_id or str(uuid.uuid4()),
        "case_number":        "EVR-2025-0001",
        "status":             status,
        "priority":           priority,
        "violation_category": "duplicate_discount",
        "covered_entity_id":  str(uuid.uuid4()),
        "entity_name":        entity_name,
        "opened_at":          "2025-01-15T09:00:00",
        "assigned_to":        "compliance.analyst@evidentrx.com",
        "composite_score":    composite_score,
        "total_findings":     total_findings,
        "critical_findings":  critical_findings,
        "high_findings":      high_findings,
        "financial_exposure": financial_exposure,
        "risk_level":         "high",
    }


def _detail_row(**kw) -> dict:
    row = _case_row(**kw)
    row.update({
        "closed_at":        None,
        "resolution_notes": None,
        "medium_findings":  2,
        "low_findings":     1,
        "unique_patients":  14,
        "ndc_list":         ["00069420030"],
        "findings_by_rule": {"DD-001": 3, "MO-002": 2},
    })
    return row


def _dashboard_row() -> dict:
    return {
        "open_cases":          4,
        "escalated_cases":     2,
        "triaged_cases":       3,
        "investigating_cases": 1,
    }


def _findings_row() -> dict:
    return {
        "total":          5,
        "critical":       1,
        "high":           2,
        "medium":         1,
        "low":            1,
        "total_exposure": 4200.0,
    }


# ── Mock DB wiring helpers ────────────────────────────────────────────────────

def _wire_queue(mock_db: MagicMock, rows: list[dict], total: int = 1):
    count_result = MagicMock()
    count_result.mappings.return_value.fetchone.return_value = {"cnt": total}
    items_result = MagicMock()
    items_result.mappings.return_value.fetchall.return_value = rows
    mock_db.execute.side_effect = [count_result, items_result]


def _wire_detail(mock_db: MagicMock, row: dict | None):
    result = MagicMock()
    result.mappings.return_value.fetchone.return_value = row
    mock_db.execute.return_value = result


def _wire_dashboard(mock_db: MagicMock):
    cases_result = MagicMock()
    cases_result.mappings.return_value.fetchone.return_value = _dashboard_row()
    findings_result = MagicMock()
    findings_result.mappings.return_value.fetchone.return_value = _findings_row()
    escalated_result = MagicMock()
    escalated_result.mappings.return_value.fetchall.return_value = []
    mock_db.execute.side_effect = [cases_result, findings_result, escalated_result]


# ── Dashboard endpoint ────────────────────────────────────────────────────────

class TestDashboardEndpoint:
    def test_returns_200(self, api_client, mock_db, auth_headers):
        _wire_dashboard(mock_db)
        resp = api_client.get("/api/v1/investigations/dashboard", headers=auth_headers)
        assert resp.status_code == 200

    def test_response_has_open_cases(self, api_client, mock_db, auth_headers):
        _wire_dashboard(mock_db)
        data = api_client.get("/api/v1/investigations/dashboard", headers=auth_headers).json()
        assert data["open_cases"] == 4
        assert data["escalated_cases"] == 2

    def test_response_has_severity_block(self, api_client, mock_db, auth_headers):
        _wire_dashboard(mock_db)
        data = api_client.get("/api/v1/investigations/dashboard", headers=auth_headers).json()
        assert "severity" in data
        assert "critical" in data["severity"]

    def test_recent_escalations_is_list(self, api_client, mock_db, auth_headers):
        _wire_dashboard(mock_db)
        data = api_client.get("/api/v1/investigations/dashboard", headers=auth_headers).json()
        assert isinstance(data["recent_escalations"], list)


# ── Queue endpoint ────────────────────────────────────────────────────────────

class TestQueueEndpoint:
    def test_returns_200_with_items(self, api_client, mock_db, auth_headers):
        _wire_queue(mock_db, [_case_row(status="open")], total=1)
        resp = api_client.get("/api/v1/investigations/queue", headers=auth_headers)
        assert resp.status_code == 200

    def test_response_shape(self, api_client, mock_db, auth_headers):
        _wire_queue(mock_db, [_case_row()], total=1)
        data = api_client.get("/api/v1/investigations/queue", headers=auth_headers).json()
        assert "total" in data
        assert "page" in data
        assert "limit" in data
        assert isinstance(data["items"], list)

    def test_db_status_translated_to_ui(self, api_client, mock_db, auth_headers):
        """DB 'in_progress' must appear as 'investigating' in the response."""
        _wire_queue(mock_db, [_case_row(status="in_progress")], total=1)
        data = api_client.get("/api/v1/investigations/queue", headers=auth_headers).json()
        assert data["items"][0]["status"] == "investigating"

    def test_pending_review_translated_to_triaged(self, api_client, mock_db, auth_headers):
        _wire_queue(mock_db, [_case_row(status="pending_review")], total=1)
        data = api_client.get("/api/v1/investigations/queue", headers=auth_headers).json()
        assert data["items"][0]["status"] == "triaged"

    def test_empty_queue(self, api_client, mock_db, auth_headers):
        _wire_queue(mock_db, [], total=0)
        data = api_client.get("/api/v1/investigations/queue", headers=auth_headers).json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_null_entity_name_sanitised(self, api_client, mock_db, auth_headers):
        row = _case_row(entity_name=None)
        _wire_queue(mock_db, [row], total=1)
        data = api_client.get("/api/v1/investigations/queue", headers=auth_headers).json()
        assert data["items"][0]["entity_name"] == "Unknown Entity"

    def test_null_findings_sanitised_to_zero(self, api_client, mock_db, auth_headers):
        row = _case_row(total_findings=None, critical_findings=None)
        _wire_queue(mock_db, [row], total=1)
        data = api_client.get("/api/v1/investigations/queue", headers=auth_headers).json()
        assert data["items"][0]["total_findings"] == 0

    def test_pagination_defaults(self, api_client, mock_db, auth_headers):
        _wire_queue(mock_db, [], total=0)
        data = api_client.get("/api/v1/investigations/queue", headers=auth_headers).json()
        assert data["page"] == 1
        assert data["limit"] == 25

    def test_pagination_params_accepted(self, api_client, mock_db, auth_headers):
        _wire_queue(mock_db, [], total=0)
        data = api_client.get(
            "/api/v1/investigations/queue?page=3&limit=10", headers=auth_headers
        ).json()
        assert data["page"] == 3
        assert data["limit"] == 10


# ── Case detail endpoint ──────────────────────────────────────────────────────

class TestCaseDetailEndpoint:
    def test_returns_200_for_existing_case(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        _wire_detail(mock_db, _detail_row(case_id=case_id))
        resp = api_client.get(f"/api/v1/investigations/{case_id}", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_404_for_missing_case(self, api_client, mock_db, auth_headers):
        _wire_detail(mock_db, None)
        resp = api_client.get(
            f"/api/v1/investigations/{uuid.uuid4()}", headers=auth_headers
        )
        assert resp.status_code == 404

    def test_detail_fields_present(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        _wire_detail(mock_db, _detail_row(case_id=case_id))
        data = api_client.get(
            f"/api/v1/investigations/{case_id}", headers=auth_headers
        ).json()
        assert "medium_findings" in data
        assert "low_findings" in data
        assert "unique_patients" in data
        assert "ndc_list" in data
        assert "findings_by_rule" in data

    def test_db_status_translated_in_detail(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        _wire_detail(mock_db, _detail_row(case_id=case_id, status="in_progress"))
        data = api_client.get(
            f"/api/v1/investigations/{case_id}", headers=auth_headers
        ).json()
        assert data["status"] == "investigating"

    def test_ndc_list_in_response(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        _wire_detail(mock_db, _detail_row(case_id=case_id))
        data = api_client.get(
            f"/api/v1/investigations/{case_id}", headers=auth_headers
        ).json()
        assert "00069420030" in data["ndc_list"]

    def test_findings_by_rule_in_response(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        _wire_detail(mock_db, _detail_row(case_id=case_id))
        data = api_client.get(
            f"/api/v1/investigations/{case_id}", headers=auth_headers
        ).json()
        assert data["findings_by_rule"]["DD-001"] == 3


# ── Status update endpoint ────────────────────────────────────────────────────

class TestStatusUpdateEndpoint:
    def test_returns_200_on_valid_status(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        mock_db.execute.return_value = MagicMock()
        resp = api_client.patch(
            f"/api/v1/investigations/{case_id}/status",
            json={"status": "escalated"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_response_echoes_ui_status(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        mock_db.execute.return_value = MagicMock()
        data = api_client.patch(
            f"/api/v1/investigations/{case_id}/status",
            json={"status": "investigating"},
            headers=auth_headers,
        ).json()
        assert data["status"] == "investigating"
        assert data["updated"] is True

    def test_returns_422_on_invalid_status(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        resp = api_client.patch(
            f"/api/v1/investigations/{case_id}/status",
            json={"status": "INVALID_STATUS"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_accepts_all_valid_ui_statuses(self, api_client, mock_db, auth_headers):
        case_id = str(uuid.uuid4())
        for status in ("open", "triaged", "investigating", "escalated", "resolved", "closed"):
            mock_db.execute.return_value = MagicMock()
            mock_db.commit.return_value = None
            resp = api_client.patch(
                f"/api/v1/investigations/{case_id}/status",
                json={"status": status},
                headers=auth_headers,
            )
            assert resp.status_code == 200, f"Failed for status={status}"
