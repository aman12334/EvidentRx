"""
Pydantic schemas for the dashboard API endpoints.
These mirror the response models in api/routers/dashboard.py
and are the canonical type definitions for the frontend.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class DashboardSummary(BaseModel):
    """Top-level KPIs for the compliance overview dashboard."""
    open_cases:          int   = Field(..., ge=0, description="Cases with status='open'")
    escalated_cases:     int   = Field(..., ge=0, description="Cases with status='escalated'")
    triaged_cases:       int   = Field(..., ge=0, description="Cases with status='triaged'")
    investigating_cases: int   = Field(..., ge=0, description="Cases with status='investigating'")
    total_findings:      int   = Field(..., ge=0, description="All open audit findings")
    critical_findings:   int   = Field(..., ge=0, description="Open findings with severity='critical'")
    high_findings:       int   = Field(..., ge=0, description="Open findings with severity='high'")
    total_exposure:      Optional[float] = Field(None, description="Sum of financial_exposure for open findings (USD)")
    avg_risk_score:      Optional[float] = Field(None, ge=0, le=1, description="Average composite_risk_score 0-1")
    covered_entities:    int   = Field(..., ge=0, description="Active covered entities in ref.covered_entities")
    uploads_this_week:   int   = Field(..., ge=0, description="Upload batches in the last 7 days")
    findings_this_week:  int   = Field(..., ge=0, description="New findings created in the last 7 days")


class RuleBreakdownItem(BaseModel):
    """Per-rule finding count with financial exposure aggregate."""
    rule_code: str           = Field(..., description="Rule code, e.g. DD-001")
    rule_name: str           = Field(..., description="Human-readable rule name")
    severity:  str           = Field(..., description="critical | high | medium | low")
    count:     int           = Field(..., ge=0)
    exposure:  Optional[float] = Field(None, description="Total financial exposure for this rule (USD)")


class RiskMatrixCell(BaseModel):
    """One cell in the status×priority heat-map."""
    status:   str = Field(..., description="open | triaged | investigating | escalated")
    priority: str = Field(..., description="critical | high | medium | low")
    count:    int = Field(..., ge=0)


class ExposureTrendPoint(BaseModel):
    """Daily financial exposure data point for trend charts."""
    date:     str   = Field(..., description="ISO-8601 date, e.g. 2025-01-15")
    exposure: float = Field(..., ge=0, description="Total financial exposure for this day (USD)")
    count:    int   = Field(..., ge=0, description="Number of findings created this day")


class CoveredEntitySummary(BaseModel):
    """Per-entity summary for the entity leaderboard."""
    ce_id:           str
    entity_name:     str
    entity_type:     str
    state_code:      str
    open_cases:      int
    total_findings:  int
    exposure:        Optional[float]
    risk_score:      Optional[float]


class WeeklyActivitySummary(BaseModel):
    """7-day activity digest for the dashboard sidebar."""
    new_findings:   int
    cases_opened:   int
    cases_resolved: int
    uploads:        int
    agent_runs:     int
    escalations:    int
