"""
Investigation case API endpoints.

Provides the investigation queue, case detail, status updates,
dashboard metrics, and severity distribution views.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from api.schemas.investigation import (
    CaseStatusUpdate,
    DashboardMetrics,
    InvestigationCaseDetail,
    InvestigationQueueResponse,
    SeverityDistribution,
)
from app.database import get_db

router = APIRouter(prefix="/investigations", tags=["Investigations"])


@router.get("/dashboard", response_model=DashboardMetrics)
def get_dashboard_metrics(db: Session = Depends(get_db)):
    """Returns top-level metrics for the analyst dashboard."""
    rows = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE ic.status = 'open')          AS open_cases,
            COUNT(*) FILTER (WHERE ic.status = 'escalated')     AS escalated_cases,
            COUNT(*) FILTER (WHERE ic.status = 'triaged')       AS triaged_cases,
            COUNT(*) FILTER (WHERE ic.status = 'investigating')  AS investigating_cases
        FROM audit.investigation_cases ic
        WHERE ic.status NOT IN ('closed', 'resolved')
    """)).mappings().fetchone()

    findings = db.execute(text("""
        SELECT
            COUNT(*)                                               AS total,
            COUNT(*) FILTER (WHERE severity = 'critical')         AS critical,
            COUNT(*) FILTER (WHERE severity = 'high')             AS high,
            COUNT(*) FILTER (WHERE severity = 'medium')           AS medium,
            COUNT(*) FILTER (WHERE severity = 'low')              AS low,
            COALESCE(SUM(
                CASE WHEN (evidence_payload->>'financial_exposure') IS NOT NULL
                THEN (evidence_payload->>'financial_exposure')::float ELSE 0 END
            ), 0) AS total_exposure
        FROM audit.audit_findings
    """)).mappings().fetchone()

    escalated = db.execute(text("""
        SELECT ic.case_id, ic.case_number, ic.status, ic.priority,
               ic.violation_category, ce.entity_name,
               ic.covered_entity_id
        FROM audit.investigation_cases ic
        LEFT JOIN ref.covered_entities ce
               ON ic.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
        WHERE ic.status = 'escalated'
        ORDER BY ic.opened_at DESC
        LIMIT 5
    """)).mappings().fetchall()

    return DashboardMetrics(
        open_cases=int(rows["open_cases"] or 0),
        escalated_cases=int(rows["escalated_cases"] or 0),
        triaged_cases=int(rows["triaged_cases"] or 0),
        investigating_cases=int(rows["investigating_cases"] or 0),
        total_findings=int(findings["total"] or 0),
        critical_findings=int(findings["critical"] or 0),
        total_exposure=float(findings["total_exposure"] or 0),
        severity=SeverityDistribution(
            critical=int(findings["critical"] or 0),
            high=int(findings["high"] or 0),
            medium=int(findings["medium"] or 0),
            low=int(findings["low"] or 0),
            total=int(findings["total"] or 0),
        ),
        recent_escalations=[dict(r) for r in escalated],
    )


@router.get("/queue", response_model=InvestigationQueueResponse)
def get_investigation_queue(
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Returns paginated investigation queue with optional status/priority filters."""
    filters = ["ic.status NOT IN ('closed', 'resolved')"]
    params: dict = {"offset": (page - 1) * limit, "limit": limit}

    if status:
        filters.append("ic.status = :status")
        params["status"] = status
    if priority:
        filters.append("ic.priority = :priority")
        params["priority"] = priority

    where = " AND ".join(filters)

    total_row = db.execute(text(f"""
        SELECT COUNT(*) AS cnt
        FROM audit.investigation_cases ic
        WHERE {where}
    """), params).mappings().fetchone()

    rows = db.execute(text(f"""
        SELECT ic.case_id, ic.case_number, ic.status, ic.priority,
               ic.violation_category, ic.covered_entity_id, ic.opened_at,
               ic.assigned_to, ce.entity_name,
               crs.risk_level, crs.composite_score,
               crs.total_findings, crs.critical_findings,
               crs.high_findings, crs.estimated_financial_exposure AS financial_exposure
        FROM audit.investigation_cases ic
        LEFT JOIN ref.covered_entities ce
               ON ic.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
        LEFT JOIN LATERAL (
            SELECT risk_level, composite_score, total_findings,
                   critical_findings, high_findings, estimated_financial_exposure
            FROM audit.case_risk_snapshots
            WHERE case_id = ic.case_id
            ORDER BY created_at DESC LIMIT 1
        ) crs ON TRUE
        WHERE {where}
        ORDER BY
            CASE ic.priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                             WHEN 'medium' THEN 3 ELSE 4 END,
            ic.opened_at DESC
        LIMIT :limit OFFSET :offset
    """), params).mappings().fetchall()

    return InvestigationQueueResponse(
        total=int(total_row["cnt"]),
        page=page,
        limit=limit,
        items=[dict(r) for r in rows],
    )


@router.get("/{case_id}", response_model=InvestigationCaseDetail)
def get_case_detail(case_id: UUID, db: Session = Depends(get_db)):
    """Returns full case detail including risk snapshot and finding breakdown."""
    row = db.execute(text("""
        SELECT ic.case_id, ic.case_number, ic.status, ic.priority,
               ic.violation_category, ic.covered_entity_id,
               ic.opened_at, ic.closed_at, ic.assigned_to, ic.resolution_notes,
               ce.entity_name,
               crs.risk_level, crs.composite_score,
               crs.total_findings, crs.critical_findings, crs.high_findings,
               crs.medium_findings, crs.low_findings,
               crs.unique_patients, crs.estimated_financial_exposure AS financial_exposure,
               crs.ndc_list, crs.findings_by_rule
        FROM audit.investigation_cases ic
        LEFT JOIN ref.covered_entities ce
               ON ic.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
        LEFT JOIN LATERAL (
            SELECT risk_level, composite_score, total_findings, critical_findings,
                   high_findings, medium_findings, low_findings, unique_patients,
                   estimated_financial_exposure, ndc_list, findings_by_rule
            FROM audit.case_risk_snapshots
            WHERE case_id = ic.case_id
            ORDER BY created_at DESC LIMIT 1
        ) crs ON TRUE
        WHERE ic.case_id = :cid::uuid
    """), {"cid": str(case_id)}).mappings().fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    return dict(row)


@router.patch("/{case_id}/status", response_model=dict)
def update_case_status(
    case_id: UUID,
    body: CaseStatusUpdate,
    db: Session = Depends(get_db),
):
    """Updates the status of an investigation case."""
    db.execute(text("""
        UPDATE audit.investigation_cases
        SET status = :status,
            resolution_notes = COALESCE(:notes, resolution_notes),
            closed_at = CASE WHEN :status IN ('resolved','closed') THEN NOW() ELSE closed_at END
        WHERE case_id = :cid::uuid
    """), {"cid": str(case_id), "status": body.status, "notes": body.resolution_notes})
    db.commit()
    return {"case_id": str(case_id), "status": body.status, "updated": True}
