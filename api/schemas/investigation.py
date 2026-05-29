"""
Typed API contracts for investigation case endpoints.
All schemas use Pydantic v2 for strict validation and serialization.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InvestigationCaseSummary(BaseModel):
    case_id:            UUID
    case_number:        str
    status:             str
    priority:           str
    violation_category: str
    entity_name:        str | None = None   # None when CE not in reference table
    covered_entity_id:  UUID
    risk_level:         str | None = None
    composite_score:    float | None = None
    total_findings:     int | None = 0
    critical_findings:  int | None = 0
    high_findings:      int | None = 0
    financial_exposure: float | None = 0.0
    opened_at:          datetime | None = None
    assigned_to:        str | None = None

    model_config = {"from_attributes": True}


class InvestigationCaseDetail(InvestigationCaseSummary):
    medium_findings:    int | None = 0
    low_findings:       int | None = 0
    unique_patients:    int | None = 0
    ndc_list:           list[str] | None = Field(default_factory=list)
    findings_by_rule:   dict[str, int] | None = Field(default_factory=dict)
    closed_at:          datetime | None = None
    resolution_notes:   str | None = None


class InvestigationQueueResponse(BaseModel):
    total:  int
    page:   int
    limit:  int
    items:  list[InvestigationCaseSummary]


class CaseStatusUpdate(BaseModel):
    # Accept both frontend labels (triaged, investigating) and DB values
    # (pending_review, in_progress) — the router translates before writing.
    status:           str = Field(
        ...,
        pattern=(
            "^(open|triaged|investigating|in_progress|pending_review"
            "|escalated|resolved|closed|dismissed|on_hold)$"
        ),
    )
    resolution_notes: str | None = None


class SeverityDistribution(BaseModel):
    critical: int = 0
    high:     int = 0
    medium:   int = 0
    low:      int = 0
    total:    int = 0


class DashboardMetrics(BaseModel):
    open_cases:         int
    escalated_cases:    int
    triaged_cases:      int
    investigating_cases: int
    total_findings:     int
    critical_findings:  int
    total_exposure:     float
    severity:           SeverityDistribution
    recent_escalations: list[InvestigationCaseSummary] = Field(default_factory=list)
