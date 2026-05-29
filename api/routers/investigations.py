"""
Investigation case API endpoints.

Provides the investigation queue, case detail, status updates,
dashboard metrics, and severity distribution views.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
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

# ── Actual DB status values ───────────────────────────────────────────────────
# open | in_progress | pending_review | escalated | closed | dismissed | on_hold
_CLOSED_STATUSES = ("'closed'", "'dismissed'")
_CLOSED_IN = ", ".join(_CLOSED_STATUSES)

# ── Status translation: DB ↔ frontend ─────────────────────────────────────────
# The DB uses snake_case compound statuses; the frontend uses single-word labels.
_DB_TO_UI = {
    "in_progress":    "investigating",
    "pending_review": "triaged",
    "dismissed":      "closed",
    "on_hold":        "open",
}
_UI_TO_DB = {v: k for k, v in _DB_TO_UI.items()}


def _to_ui(status: str | None) -> str:
    """Map a DB status value to the frontend label."""
    return _DB_TO_UI.get(status or "", status or "open")


def _to_db(status: str | None) -> str:
    """Map a frontend status label to the DB value."""
    return _UI_TO_DB.get(status or "", status or "open")


def _sanitise_case(d: dict) -> dict:
    """
    Coerce NULL values from DB lateral joins to safe defaults so that
    Pydantic validation never receives None for non-Optional fields.
    Also translates DB status → frontend status.
    """
    d["status"]            = _to_ui(d.get("status"))
    d["entity_name"]       = d.get("entity_name") or "Unknown Entity"
    d["total_findings"]    = int(d.get("total_findings") or 0)
    d["critical_findings"] = int(d.get("critical_findings") or 0)
    d["high_findings"]     = int(d.get("high_findings") or 0)
    d["financial_exposure"] = float(d.get("financial_exposure") or 0.0)
    return d


def _sanitise_detail(d: dict) -> dict:
    """Extends _sanitise_case with detail-only fields."""
    d = _sanitise_case(d)
    d["medium_findings"]  = int(d.get("medium_findings") or 0)
    d["low_findings"]     = int(d.get("low_findings") or 0)
    d["unique_patients"]  = int(d.get("unique_patients") or 0)
    d["ndc_list"]         = d.get("ndc_list") or []
    d["findings_by_rule"] = d.get("findings_by_rule") or {}
    return d

# ── Reusable risk-level CASE expression ───────────────────────────────────────
_RISK_LEVEL = """
    CASE
        WHEN crs.composite_risk_score >= 0.8 THEN 'critical'
        WHEN crs.composite_risk_score >= 0.6 THEN 'high'
        WHEN crs.composite_risk_score >= 0.3 THEN 'medium'
        WHEN crs.composite_risk_score IS NOT NULL THEN 'low'
        ELSE NULL
    END
""".strip()


@router.get("/dashboard", response_model=DashboardMetrics)
def get_dashboard_metrics(db: Session = Depends(get_db)):
    """Returns top-level metrics for the analyst dashboard."""
    rows = db.execute(text(f"""
        SELECT
            COUNT(*) FILTER (WHERE ic.status = 'open')          AS open_cases,
            COUNT(*) FILTER (WHERE ic.status = 'escalated')     AS escalated_cases,
            COUNT(*) FILTER (WHERE ic.status = 'pending_review') AS triaged_cases,
            COUNT(*) FILTER (WHERE ic.status = 'in_progress')   AS investigating_cases
        FROM audit.investigation_cases ic
        WHERE ic.status NOT IN ({_CLOSED_IN})
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
               ic.case_type AS violation_category,
               ic.covered_entity_id, ce.entity_name
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
        recent_escalations=[_sanitise_case(dict(r)) for r in escalated],
    )


@router.get("/queue", response_model=InvestigationQueueResponse)
def get_investigation_queue(
    status: str | None = Query(None, description="Filter by status"),
    priority: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Returns paginated investigation queue with optional status/priority filters."""
    filters = [f"ic.status NOT IN ({_CLOSED_IN})"]
    params: dict = {"offset": (page - 1) * limit, "limit": limit}

    if status:
        params["status"] = _to_db(status)
        filters.append("ic.status = :status")
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
               ic.case_type          AS violation_category,
               ic.covered_entity_id,
               ic.opened_at,
               ic.assigned_to,
               ce.entity_name,
               crs.composite_risk_score    AS composite_score,
               crs.total_findings,
               crs.critical_findings,
               crs.high_findings,
               crs.total_financial_exposure AS financial_exposure,
               {_RISK_LEVEL}               AS risk_level
        FROM audit.investigation_cases ic
        LEFT JOIN ref.covered_entities ce
               ON ic.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
        LEFT JOIN LATERAL (
            SELECT composite_risk_score, total_findings,
                   critical_findings, high_findings, total_financial_exposure
            FROM audit.case_risk_snapshots
            WHERE case_id = ic.case_id
            ORDER BY snapshot_at DESC LIMIT 1
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
        items=[_sanitise_case(dict(r)) for r in rows],
    )


@router.get("/{case_id}", response_model=InvestigationCaseDetail)
def get_case_detail(case_id: UUID, db: Session = Depends(get_db)):
    """Returns full case detail including risk snapshot and finding breakdown."""
    row = db.execute(text(f"""
        SELECT ic.case_id, ic.case_number, ic.status, ic.priority,
               ic.case_type          AS violation_category,
               ic.covered_entity_id,
               ic.opened_at,
               ic.closed_at,
               ic.assigned_to,
               ic.description        AS resolution_notes,
               ce.entity_name,
               crs.composite_risk_score    AS composite_score,
               crs.total_findings,
               crs.critical_findings,
               crs.high_findings,
               crs.medium_findings,
               crs.low_findings,
               crs.unique_patients,
               crs.total_financial_exposure AS financial_exposure,
               crs.ndc_list,
               crs.findings_by_rule,
               {_RISK_LEVEL}               AS risk_level
        FROM audit.investigation_cases ic
        LEFT JOIN ref.covered_entities ce
               ON ic.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
        LEFT JOIN LATERAL (
            SELECT composite_risk_score, total_findings, critical_findings,
                   high_findings, medium_findings, low_findings,
                   unique_patients, total_financial_exposure,
                   ndc_list, findings_by_rule
            FROM audit.case_risk_snapshots
            WHERE case_id = ic.case_id
            ORDER BY snapshot_at DESC LIMIT 1
        ) crs ON TRUE
        WHERE ic.case_id = :cid::uuid
    """), {"cid": str(case_id)}).mappings().fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    return _sanitise_detail(dict(row))


@router.patch("/{case_id}/status", response_model=dict)
def update_case_status(
    case_id: UUID,
    body: CaseStatusUpdate,
    db: Session = Depends(get_db),
):
    """Updates the status of an investigation case."""
    db_status = _to_db(body.status)
    db.execute(text("""
        UPDATE audit.investigation_cases
        SET status    = :status,
            closed_at = CASE WHEN :status IN ('closed','dismissed') THEN NOW()
                             ELSE closed_at END,
            updated_at = NOW()
        WHERE case_id = :cid::uuid
    """), {"cid": str(case_id), "status": db_status})
    db.commit()
    # Return the UI-facing status so the frontend cache stays consistent
    return {"case_id": str(case_id), "status": body.status, "updated": True}
