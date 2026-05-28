"""
entities.py — Covered entity management endpoints.

Provides read access to the ref.covered_entities table for the
investigation workspace. Write operations (add/deactivate entities)
require admin privileges and go through HRSA OPAIS data sync.

Endpoints:
  GET /entities                    — Paginated list with search
  GET /entities/{ce_id}            — Full entity detail
  GET /entities/{ce_id}/cases      — Open investigation cases for a CE
  GET /entities/{ce_id}/summary    — KPI summary for a CE
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/entities", tags=["Covered Entities"])


# ── Pydantic models ───────────────────────────────────────────────────────────


class CoveredEntity(BaseModel):
    ce_id:             str
    hrsa_id:           str
    entity_name:       str
    entity_type_code:  Optional[str]
    entity_type_description: Optional[str]
    city:              Optional[str]
    state_code:        Optional[str]
    zip_code:          Optional[str]
    npi:               Optional[str]
    primary_340b_program: Optional[str]
    program_status:    str
    program_participation_start: Optional[str]
    is_active:         bool


class EntitySummary(BaseModel):
    ce_id:          str
    entity_name:    str
    open_cases:     int
    total_findings: int
    critical_findings: int
    total_exposure: Optional[float]
    avg_risk_score: Optional[float]


class EntityListResponse(BaseModel):
    entities:    list[CoveredEntity]
    total:       int
    page:        int
    limit:       int


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("", response_model=EntityListResponse)
def list_entities(
    search:      Optional[str] = Query(None,  description="Search by name or HRSA ID"),
    state_code:  Optional[str] = Query(None,  description="Filter by 2-letter state code"),
    entity_type: Optional[str] = Query(None,  description="Filter by entity_type_code e.g. DSH, CHC"),
    active_only: bool           = Query(True,  description="Only return active entities"),
    page:        int            = Query(1, ge=1),
    limit:       int            = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> EntityListResponse:
    """Paginated, searchable covered entity list."""
    conditions = ["ce.is_current = TRUE"]
    params: dict = {"limit": limit, "offset": (page - 1) * limit}

    if active_only:
        conditions.append("ce.is_active = TRUE")
    if search:
        conditions.append("(ce.entity_name ILIKE :search OR ce.hrsa_id ILIKE :search)")
        params["search"] = f"%{search}%"
    if state_code:
        conditions.append("ce.state_code = :state")
        params["state"] = state_code.upper()
    if entity_type:
        conditions.append("ce.entity_type_code = :etype")
        params["etype"] = entity_type.upper()

    where = "WHERE " + " AND ".join(conditions)

    total_row = db.execute(text(f"""
        SELECT COUNT(*) FROM ref.covered_entities ce {where}
    """), params).scalar() or 0

    rows = db.execute(text(f"""
        SELECT
            ce.ce_id::text, ce.hrsa_id, ce.entity_name,
            ce.entity_type_code, ce.entity_type_description,
            ce.city, ce.state_code, ce.zip_code, ce.npi,
            ce.primary_340b_program, ce.program_status,
            ce.program_participation_start::text,
            ce.is_active
        FROM ref.covered_entities ce
        {where}
        ORDER BY ce.entity_name ASC
        LIMIT :limit OFFSET :offset
    """), params).mappings().fetchall()

    entities = [
        CoveredEntity(
            ce_id=r["ce_id"],
            hrsa_id=r["hrsa_id"],
            entity_name=r["entity_name"],
            entity_type_code=r["entity_type_code"],
            entity_type_description=r["entity_type_description"],
            city=r["city"],
            state_code=r["state_code"],
            zip_code=r["zip_code"],
            npi=r["npi"],
            primary_340b_program=r["primary_340b_program"],
            program_status=r["program_status"],
            program_participation_start=r["program_participation_start"],
            is_active=r["is_active"],
        )
        for r in rows
    ]

    return EntityListResponse(
        entities=entities,
        total=total_row,
        page=page,
        limit=limit,
    )


@router.get("/{ce_id}", response_model=CoveredEntity)
def get_entity(ce_id: UUID, db: Session = Depends(get_db)) -> CoveredEntity:
    """Full detail for a single covered entity."""
    row = db.execute(text("""
        SELECT
            ce_id::text, hrsa_id, entity_name,
            entity_type_code, entity_type_description,
            city, state_code, zip_code, npi,
            primary_340b_program, program_status,
            program_participation_start::text, is_active
        FROM ref.covered_entities
        WHERE ce_id = :ce_id AND is_current = TRUE
    """), {"ce_id": str(ce_id)}).mappings().fetchone()

    if not row:
        raise HTTPException(404, f"Covered entity {ce_id} not found")

    return CoveredEntity(**dict(row))


@router.get("/{ce_id}/summary", response_model=EntitySummary)
def get_entity_summary(ce_id: UUID, db: Session = Depends(get_db)) -> EntitySummary:
    """KPI summary for a single covered entity."""
    # Verify CE exists
    exists = db.execute(
        text("SELECT 1 FROM ref.covered_entities WHERE ce_id = :id AND is_current"),
        {"id": str(ce_id)},
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Covered entity {ce_id} not found")

    name = db.execute(
        text("SELECT entity_name FROM ref.covered_entities WHERE ce_id = :id AND is_current"),
        {"id": str(ce_id)},
    ).scalar() or ""

    row = db.execute(text("""
        SELECT
            COUNT(DISTINCT ic.case_id) FILTER (WHERE ic.status NOT IN ('closed','resolved')) AS open_cases,
            COUNT(af.finding_id)                                            AS total_findings,
            COUNT(af.finding_id) FILTER (WHERE af.severity = 'critical')   AS critical_findings,
            SUM(af.financial_exposure)                                      AS total_exposure,
            AVG(crs.composite_risk_score)                                   AS avg_risk
        FROM audit.investigation_cases ic
        LEFT JOIN audit.investigation_case_findings icf ON icf.case_id = ic.case_id
        LEFT JOIN audit.audit_findings af ON af.finding_id = icf.finding_id
        LEFT JOIN LATERAL (
            SELECT composite_risk_score FROM audit.case_risk_snapshots
            WHERE case_id = ic.case_id ORDER BY snapshot_at DESC LIMIT 1
        ) crs ON TRUE
        WHERE ic.covered_entity_id = :ce_id
    """), {"ce_id": str(ce_id)}).fetchone()

    return EntitySummary(
        ce_id=str(ce_id),
        entity_name=name,
        open_cases=     row.open_cases or 0,
        total_findings= row.total_findings or 0,
        critical_findings= row.critical_findings or 0,
        total_exposure= float(row.total_exposure) if row.total_exposure else None,
        avg_risk_score= float(row.avg_risk) if row.avg_risk else None,
    )
