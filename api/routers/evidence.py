"""Evidence lineage and transaction chain API endpoints."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.schemas.evidence import EvidenceChain, EvidenceSnapshot
from app.database import get_db

router = APIRouter(prefix="/evidence", tags=["Evidence"])


@router.get("/case/{case_id}", response_model=EvidenceSnapshot)
def get_evidence_snapshot(case_id: UUID, db: Session = Depends(get_db)):
    """
    Returns the full evidence snapshot for a case —
    all transaction chains, linked pharmacies, NDCs, and entities.
    """
    findings = db.execute(text("""
        SELECT af.finding_id, af.finding_code, af.rule_code, af.severity,
               af.evidence_payload, sb.ndc_11,
               sb.split_billing_id
        FROM audit.investigation_case_findings icf
        JOIN audit.audit_findings af ON icf.finding_id = af.finding_id
        LEFT JOIN ops.split_billing sb ON af.split_billing_id = sb.split_billing_id
        WHERE icf.case_id = :cid::uuid
        ORDER BY af.severity DESC
        LIMIT 100
    """), {"cid": str(case_id)}).mappings().fetchall()

    if not findings:
        raise HTTPException(status_code=404, detail=f"No evidence found for case {case_id}")

    chains = []
    pharmacies = set()
    ndcs = set()
    entities = set()

    for f in findings:
        ev = f["evidence_payload"] or {}
        pharmacy_id = ev.get("pharmacy_id") or ev.get("contract_pharmacy_id")
        pharmacy_name = ev.get("pharmacy_name")
        ndc = f["ndc_11"] or ev.get("ndc_11")

        if pharmacy_id:
            pharmacies.add(str(pharmacy_id))
        if ndc:
            ndcs.add(ndc)
        if ev.get("covered_entity_id"):
            entities.add(str(ev["covered_entity_id"]))

        chains.append(EvidenceChain(
            finding_id=f["finding_id"],
            rule_code=f["rule_code"],
            severity=f["severity"],
            split_billing_id=f["split_billing_id"],
            pharmacy_id=pharmacy_id,
            pharmacy_name=pharmacy_name,
            ndc_11=ndc,
            notes=ev.get("notes", ""),
        ))

    return EvidenceSnapshot(
        case_id=case_id,
        total_findings=len(chains),
        chains=chains,
        linked_pharmacies=sorted(pharmacies),
        linked_ndcs=sorted(ndcs),
        linked_entities=sorted(entities),
    )


@router.get("/finding/{finding_id}/chain", response_model=EvidenceChain)
def get_finding_chain(finding_id: UUID, db: Session = Depends(get_db)):
    """Returns the transaction chain for a single finding."""
    row = db.execute(text("""
        SELECT af.finding_id, af.rule_code, af.severity,
               af.evidence_payload, sb.ndc_11, sb.split_billing_id
        FROM audit.audit_findings af
        LEFT JOIN ops.split_billing sb ON af.split_billing_id = sb.split_billing_id
        WHERE af.finding_id = :fid::uuid
    """), {"fid": str(finding_id)}).mappings().fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Finding {finding_id} not found")

    ev = row["evidence_payload"] or {}
    return EvidenceChain(
        finding_id=row["finding_id"],
        rule_code=row["rule_code"],
        severity=row["severity"],
        split_billing_id=row["split_billing_id"],
        pharmacy_id=ev.get("pharmacy_id"),
        pharmacy_name=ev.get("pharmacy_name"),
        ndc_11=row["ndc_11"] or ev.get("ndc_11"),
        notes=ev.get("notes", ""),
    )
