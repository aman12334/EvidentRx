"""
Typed API contracts for investigation case endpoints.
All schemas use Pydantic v2 for strict validation and serialization.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class InvestigationCaseSummary(BaseModel):
    case_id:            UUID
    case_number:        str
    status:             str
    priority:           str
    violation_category: str
    entity_name:        str
    covered_entity_id:  UUID
    risk_level:         Optional[str] = None
    composite_score:    Optional[float] = None
    total_findings:     int = 0
    critical_findings:  int = 0
    high_findings:      int = 0
    financial_exposure: float = 0.0
    opened_at:          Optional[datetime] = None
    assigned_to:        Optional[str] = None

    model_config = {"from_attributes": True}


class InvestigationCaseDetail(InvestigationCaseSummary):
    medium_findings:    int = 0
    low_findings:       int = 0
    unique_patients:    int = 0
    ndc_list:           list[str] = Field(default_factory=list)
    findings_by_rule:   dict[str, int] = Field(default_factory=dict)
    closed_at:          Optional[datetime] = None
    resolution_notes:   Optional[str] = None


class InvestigationQueueResponse(BaseModel):
    total:  int
    page:   int
    limit:  int
    items:  list[InvestigationCaseSummary]


class CaseStatusUpdate(BaseModel):
    status:           str = Field(..., pattern="^(open|triaged|investigating|escalated|resolved|closed)$")
    resolution_notes: Optional[str] = None


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
