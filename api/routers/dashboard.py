"""
dashboard.py — Dashboard summary and KPI aggregation endpoints.

Provides the data layer for the main compliance overview dashboard.
All queries are read-only and optimised for dashboard-level latency (<200ms).

Endpoints:
  GET /api/v1/dashboard/summary     — Top-level KPIs
  GET /api/v1/dashboard/risk-matrix — Case distribution by priority × status
  GET /api/v1/dashboard/rule-breakdown — Findings grouped by rule code
  GET /api/v1/dashboard/exposure-trend — Financial exposure over rolling 90 days
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

# ── Pydantic response models ──────────────────────────────────────────────────


class DashboardSummary(BaseModel):
    open_cases:          int
    escalated_cases:     int
    triaged_cases:       int
    investigating_cases: int
    total_findings:      int
    critical_findings:   int
    high_findings:       int
    total_exposure:      Optional[float]
    avg_risk_score:      Optional[float]
    covered_entities:    int
    uploads_this_week:   int
    findings_this_week:  int


class RuleBreakdownItem(BaseModel):
    rule_code:   str
    rule_name:   str
    severity:    str
    count:       int
    exposure:    Optional[float]


class RiskMatrixCell(BaseModel):
    status:   str
    priority: str
    count:    int


class ExposureTrendPoint(BaseModel):
    date:     str         # ISO-8601 date
    exposure: float
    count:    int         # findings created that day


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(db: Session = Depends(get_db)) -> DashboardSummary:
    """
    Returns top-level KPIs for the compliance dashboard.
    Single query via aggregate CTEs for sub-100ms response.
    """
    row = db.execute(text("""
        WITH case_stats AS (
            SELECT
                COUNT(*) FILTER (WHERE status = 'open')          AS open_cases,
                COUNT(*) FILTER (WHERE status = 'escalated')     AS escalated_cases,
                COUNT(*) FILTER (WHERE status = 'triaged')       AS triaged_cases,
                COUNT(*) FILTER (WHERE status = 'investigating') AS investigating_cases,
                AVG(
                    CASE WHEN crs.composite_risk_score IS NOT NULL
                    THEN crs.composite_risk_score ELSE NULL END
                ) AS avg_risk
            FROM audit.investigation_cases ic
            LEFT JOIN LATERAL (
                SELECT composite_risk_score
                FROM audit.case_risk_snapshots
                WHERE case_id = ic.case_id
                ORDER BY snapshot_at DESC
                LIMIT 1
            ) crs ON TRUE
            WHERE ic.status NOT IN ('closed', 'resolved')
        ),
        finding_stats AS (
            SELECT
                COUNT(*)                                          AS total,
                COUNT(*) FILTER (WHERE severity = 'critical')    AS critical,
                COUNT(*) FILTER (WHERE severity = 'high')        AS high,
                SUM(financial_exposure)                           AS exposure
            FROM audit.audit_findings
            WHERE status = 'open'
        ),
        ce_count AS (
            SELECT COUNT(*) AS n FROM ref.covered_entities WHERE is_current = TRUE
        ),
        upload_stats AS (
            SELECT
                COUNT(*)                  AS uploads,
                COALESCE(SUM(record_count), 0) AS records
            FROM meta.ingestion_batches
            WHERE source_system = 'upload'
              AND started_at >= NOW() - INTERVAL '7 days'
        ),
        findings_week AS (
            SELECT COUNT(*) AS n
            FROM audit.audit_findings
            WHERE created_at >= NOW() - INTERVAL '7 days'
        )
        SELECT
            cs.open_cases, cs.escalated_cases, cs.triaged_cases,
            cs.investigating_cases, cs.avg_risk,
            fs.total, fs.critical, fs.high, fs.exposure,
            cc.n AS ce_count,
            us.uploads,
            fw.n AS findings_week
        FROM case_stats cs, finding_stats fs, ce_count cc,
             upload_stats us, findings_week fw
    """)).fetchone()

    if not row:
        return DashboardSummary(
            open_cases=0, escalated_cases=0, triaged_cases=0,
            investigating_cases=0, total_findings=0, critical_findings=0,
            high_findings=0, total_exposure=None, avg_risk_score=None,
            covered_entities=0, uploads_this_week=0, findings_this_week=0,
        )

    return DashboardSummary(
        open_cases=          row.open_cases or 0,
        escalated_cases=     row.escalated_cases or 0,
        triaged_cases=       row.triaged_cases or 0,
        investigating_cases= row.investigating_cases or 0,
        total_findings=      row.total or 0,
        critical_findings=   row.critical or 0,
        high_findings=       row.high or 0,
        total_exposure=      float(row.exposure) if row.exposure else None,
        avg_risk_score=      float(row.avg_risk) if row.avg_risk else None,
        covered_entities=    row.ce_count or 0,
        uploads_this_week=   row.uploads or 0,
        findings_this_week=  row.findings_week or 0,
    )


@router.get("/rule-breakdown", response_model=list[RuleBreakdownItem])
def get_rule_breakdown(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[RuleBreakdownItem]:
    """Top-N rules by finding count with financial exposure."""
    rows = db.execute(text("""
        SELECT
            af.rule_code,
            COALESCE(cr.rule_name, af.rule_code) AS rule_name,
            af.severity,
            COUNT(*)                AS cnt,
            SUM(af.financial_exposure) AS exposure
        FROM audit.audit_findings af
        LEFT JOIN audit.compliance_rules cr ON cr.rule_code = af.rule_code
        WHERE af.status = 'open'
        GROUP BY af.rule_code, cr.rule_name, af.severity
        ORDER BY cnt DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()

    return [
        RuleBreakdownItem(
            rule_code=r.rule_code,
            rule_name=r.rule_name,
            severity=r.severity,
            count=r.cnt,
            exposure=float(r.exposure) if r.exposure else None,
        )
        for r in rows
    ]


@router.get("/risk-matrix", response_model=list[RiskMatrixCell])
def get_risk_matrix(db: Session = Depends(get_db)) -> list[RiskMatrixCell]:
    """Case counts cross-tabulated by status × priority."""
    rows = db.execute(text("""
        SELECT status, priority, COUNT(*) AS cnt
        FROM audit.investigation_cases
        WHERE status NOT IN ('closed', 'resolved')
        GROUP BY status, priority
        ORDER BY
            CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                          WHEN 'medium' THEN 3 ELSE 4 END,
            status
    """)).fetchall()

    return [
        RiskMatrixCell(status=r.status, priority=r.priority, count=r.cnt)
        for r in rows
    ]


@router.get("/exposure-trend", response_model=list[ExposureTrendPoint])
def get_exposure_trend(
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
) -> list[ExposureTrendPoint]:
    """
    Rolling financial exposure by day over the last N days.
    Useful for sparklines and trend charts on the dashboard.
    """
    since = date.today() - timedelta(days=days)
    rows = db.execute(text("""
        SELECT
            DATE(created_at)              AS day,
            COALESCE(SUM(financial_exposure), 0) AS exposure,
            COUNT(*)                      AS cnt
        FROM audit.audit_findings
        WHERE created_at >= :since
        GROUP BY DATE(created_at)
        ORDER BY day ASC
    """), {"since": since}).fetchall()

    return [
        ExposureTrendPoint(
            date=str(r.day),
            exposure=float(r.exposure),
            count=r.cnt,
        )
        for r in rows
    ]
