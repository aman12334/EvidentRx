"""Finding retrieval API endpoints."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.schemas.finding import FindingDetail, FindingListResponse, FindingsByRule
from app.database import get_db

router = APIRouter(prefix="/findings", tags=["Findings"])


@router.get("/case/{case_id}", response_model=FindingListResponse)
def get_findings_for_case(
    case_id: UUID,
    severity: str | None = Query(None),
    rule_code: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Returns paginated findings for a case with optional severity/rule filters."""
    filters = ["icf.case_id = :cid::uuid"]
    params: dict = {"cid": str(case_id), "limit": limit, "offset": (page - 1) * limit}

    if severity:
        filters.append("af.severity = :severity")
        params["severity"] = severity
    if rule_code:
        filters.append("af.rule_code = :rule_code")
        params["rule_code"] = rule_code

    where = " AND ".join(filters)

    total = db.execute(text(f"""
        SELECT COUNT(*) AS cnt
        FROM audit.investigation_case_findings icf
        JOIN audit.audit_findings af ON icf.finding_id = af.finding_id
        WHERE {where}
    """), params).mappings().fetchone()

    rows = db.execute(text(f"""
        SELECT af.finding_id, icf.case_id, af.finding_code, af.rule_code,
               af.severity, af.covered_entity_id, af.evidence_payload,
               af.created_at, ce.entity_name
        FROM audit.investigation_case_findings icf
        JOIN audit.audit_findings af ON icf.finding_id = af.finding_id
        LEFT JOIN ref.covered_entities ce
               ON af.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
        WHERE {where}
        ORDER BY
            CASE af.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                             WHEN 'medium' THEN 3 ELSE 4 END,
            af.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).mappings().fetchall()

    return FindingListResponse(
        total=int(total["cnt"]),
        page=page,
        limit=limit,
        items=[dict(r) for r in rows],
    )


@router.get("/case/{case_id}/by-rule", response_model=list[FindingsByRule])
def get_findings_by_rule(case_id: UUID, db: Session = Depends(get_db)):
    """Returns finding counts grouped by rule code for a case."""
    rows = db.execute(text("""
        SELECT af.rule_code,
               COUNT(*) AS count,
               COUNT(*) FILTER (WHERE af.severity = 'critical') AS critical,
               COUNT(*) FILTER (WHERE af.severity = 'high')     AS high,
               COUNT(*) FILTER (WHERE af.severity = 'medium')   AS medium,
               COUNT(*) FILTER (WHERE af.severity = 'low')      AS low,
               COALESCE(SUM(
                   CASE WHEN (af.evidence_payload->>'financial_exposure') IS NOT NULL
                   THEN (af.evidence_payload->>'financial_exposure')::float ELSE 0 END
               ), 0) AS exposure
        FROM audit.investigation_case_findings icf
        JOIN audit.audit_findings af ON icf.finding_id = af.finding_id
        WHERE icf.case_id = :cid::uuid
        GROUP BY af.rule_code
        ORDER BY count DESC
    """), {"cid": str(case_id)}).mappings().fetchall()

    return [dict(r) for r in rows]


@router.get("/{finding_id}", response_model=FindingDetail)
def get_finding_detail(finding_id: UUID, db: Session = Depends(get_db)):
    """Returns full detail for a single finding."""
    row = db.execute(text("""
        SELECT af.finding_id, af.finding_code, af.rule_code, af.severity,
               af.covered_entity_id, af.evidence_payload, af.entity_references,
               af.created_at, ce.entity_name,
               sb.ndc_11
        FROM audit.audit_findings af
        LEFT JOIN ref.covered_entities ce
               ON af.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
        LEFT JOIN ops.split_billing sb ON af.split_billing_id = sb.split_billing_id
        WHERE af.finding_id = :fid::uuid
    """), {"fid": str(finding_id)}).mappings().fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Finding {finding_id} not found")
    return dict(row)
