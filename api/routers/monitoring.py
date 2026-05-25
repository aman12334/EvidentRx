"""Intelligence monitoring and risk API endpoints."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.schemas.monitoring import IntelligenceSummaryResponse
from app.database import get_db

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])


@router.get("/summary", response_model=IntelligenceSummaryResponse)
def get_intelligence_summary(
    window: str = Query("30d", pattern="^(30d|60d|90d)$"),
    db: Session = Depends(get_db),
):
    """Returns the intelligence summary — top risks, trends, correlations, drift."""
    as_of = date.today()

    risk_rows = db.execute(text("""
        SELECT entity_id, entity_type, score_date, composite_score, risk_tier,
               finding_velocity, exposure_trajectory, escalation_probability, trend_direction
        FROM audit.entity_risk_scores
        WHERE score_date = (SELECT MAX(score_date) FROM audit.entity_risk_scores)
          AND entity_type = 'covered_entity'
        ORDER BY composite_score DESC
        LIMIT 10
    """)).mappings().fetchall()

    trend_rows = db.execute(text("""
        SELECT entity_id, entity_type, rule_code, window_type,
               finding_count, critical_count, risk_score,
               trend_direction, velocity, acceleration, prior_period_count
        FROM audit.compliance_trends
        WHERE trend_direction IN ('worsening', 'critical')
          AND window_type = :wt
        ORDER BY risk_score DESC
        LIMIT 10
    """), {"wt": window}).mappings().fetchall()

    corr_rows = db.execute(text("""
        SELECT case_id_a::text, case_id_b::text, correlation_type,
               strength, explanation, shared_entities
        FROM audit.cross_case_correlations
        WHERE strength >= 0.5
        ORDER BY strength DESC
        LIMIT 10
    """)).mappings().fetchall()

    drift_rows = db.execute(text("""
        SELECT run_id, run_type, status, findings_evaluated,
               new_findings, drifts_detected, correlations_found,
               started_at, completed_at
        FROM audit.monitoring_runs
        WHERE status = 'completed'
        ORDER BY completed_at DESC
        LIMIT 1
    """)).mappings().fetchone()

    return IntelligenceSummaryResponse(
        as_of=as_of,
        top_risk_entities=[dict(r) for r in risk_rows],
        worsening_trends=[dict(r) for r in trend_rows],
        high_correlations=[dict(r) for r in corr_rows],
        critical_drift_signals=[],
        last_monitoring_run=dict(drift_rows) if drift_rows else None,
    )


@router.get("/runs")
def list_monitoring_runs(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Lists recent monitoring runs."""
    rows = db.execute(text("""
        SELECT run_id, run_type, status, findings_evaluated,
               new_findings, drifts_detected, correlations_found,
               started_at, completed_at, error_message
        FROM audit.monitoring_runs
        ORDER BY started_at DESC
        LIMIT :lim
    """), {"lim": limit}).mappings().fetchall()
    return [dict(r) for r in rows]


@router.get("/risk/entities")
def get_entity_risk_scores(
    tier: Optional[str] = Query(None, pattern="^(critical|high|medium|low)$"),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Returns latest entity risk scores, optionally filtered by tier."""
    filters = ["score_date = (SELECT MAX(score_date) FROM audit.entity_risk_scores)"]
    params: dict = {"lim": limit}
    if tier:
        filters.append("risk_tier = :tier")
        params["tier"] = tier

    rows = db.execute(text(f"""
        SELECT entity_id, entity_type, score_date, composite_score, risk_tier,
               finding_velocity, exposure_trajectory, escalation_probability, trend_direction
        FROM audit.entity_risk_scores
        WHERE {" AND ".join(filters)}
        ORDER BY composite_score DESC
        LIMIT :lim
    """), params).mappings().fetchall()
    return [dict(r) for r in rows]
