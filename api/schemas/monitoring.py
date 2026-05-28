"""Typed API contracts for intelligence monitoring endpoints."""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TrendRecordSchema(BaseModel):
    entity_id:          str
    entity_type:        str
    rule_code:          str
    window_type:        str
    finding_count:      int
    critical_count:     int
    risk_score:         float
    trend_direction:    str
    velocity:           float
    acceleration:       float
    prior_period_count: int


class EntityRiskScoreSchema(BaseModel):
    entity_id:              str
    entity_type:            str
    score_date:             date
    composite_score:        float
    risk_tier:              str
    finding_velocity:       float
    exposure_trajectory:    float
    escalation_probability: float
    trend_direction:        str


class CorrelationSchema(BaseModel):
    case_id_a:          str
    case_id_b:          str
    correlation_type:   str
    strength:           float
    explanation:        str
    shared_entities:    dict = Field(default_factory=dict)


class DriftSignalSchema(BaseModel):
    drift_type:    str
    subject_id:    str
    subject_label: str
    magnitude:     str
    direction:     str
    change_pct:    float
    explanation:   str


class MonitoringRunSchema(BaseModel):
    run_id:               UUID
    run_type:             str
    status:               str
    findings_evaluated:   int = 0
    new_findings:         int = 0
    drifts_detected:      int = 0
    correlations_found:   int = 0
    started_at:           datetime | None = None
    completed_at:         datetime | None = None

    model_config = {"from_attributes": True}


class IntelligenceSummaryResponse(BaseModel):
    as_of:                  date
    top_risk_entities:      list[EntityRiskScoreSchema] = Field(default_factory=list)
    worsening_trends:       list[TrendRecordSchema] = Field(default_factory=list)
    high_correlations:      list[CorrelationSchema] = Field(default_factory=list)
    critical_drift_signals: list[DriftSignalSchema] = Field(default_factory=list)
    last_monitoring_run:    MonitoringRunSchema | None = None
