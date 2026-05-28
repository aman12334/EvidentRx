"""
Builds AuditFinding dicts from a RuleContext + ComplianceRule.
All fields except investigation_case_id and financial_exposure are set here.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from rules_engine.context import RuleContext

_SEVERITY_CONFIDENCE = {
    "critical": Decimal("1.0"),
    "high": Decimal("1.0"),
    "medium": Decimal("0.9"),
    "low": Decimal("0.8"),
}

# Global counter per run — reset each RulesEngine.run() call
_finding_counter: dict[str, int] = {}


def next_finding_code(rule_code: str, year: int) -> str:
    key = f"{rule_code}:{year}"
    _finding_counter[key] = _finding_counter.get(key, 0) + 1
    return f"{rule_code}-{year}-{_finding_counter[key]:06d}"


def reset_counters() -> None:
    _finding_counter.clear()


def build_finding(
    ctx: RuleContext,
    rule_id: UUID,
    rule_code: str,
    rule_version: str,
    rule_category: str,
    severity: str,
    evidence_extra: dict | None = None,
) -> dict:
    year = ctx.service_date.year
    finding_code = next_finding_code(rule_code, year)
    now = datetime.now(UTC).isoformat()

    evidence: dict = {
        "split_billing_id": str(ctx.split_billing_id),
        "ndc_11": ctx.ndc_11,
        "service_date": ctx.service_date.isoformat(),
        "patient_id_hash": ctx.patient_id_hash,
        "is_340b_purchase": ctx.is_340b_purchase,
        "is_medicaid_billed": ctx.is_medicaid_billed,
        "carve_in_flag": ctx.carve_in_flag,
        "accumulator_balance": str(ctx.accumulator_balance) if ctx.accumulator_balance is not None else None,
        "has_carve_out_election": ctx.has_carve_out_election,
        "cp_registered": ctx.cp_registered,
        "ce_program_start": ctx.ce_program_start.isoformat() if ctx.ce_program_start else None,
        "ce_program_end": ctx.ce_program_end.isoformat() if ctx.ce_program_end else None,
    }
    if evidence_extra:
        evidence.update(evidence_extra)

    entity_refs: dict = {
        "covered_entity_id": str(ctx.covered_entity_id),
    }
    if ctx.purchase_id:
        entity_refs["purchase_id"] = str(ctx.purchase_id)
        entity_refs["purchase_date"] = ctx.purchase_date.isoformat() if ctx.purchase_date else None
    if ctx.dispense_id:
        entity_refs["dispense_id"] = str(ctx.dispense_id)
        entity_refs["dispense_date"] = ctx.dispense_date.isoformat() if ctx.dispense_date else None
    if ctx.claim_id:
        entity_refs["claim_id"] = str(ctx.claim_id)
        entity_refs["claim_service_date"] = ctx.claim_service_date.isoformat() if ctx.claim_service_date else None

    return {
        "finding_id": str(uuid4()),
        "finding_code": finding_code,
        "rule_id": str(rule_id),
        "rule_code": rule_code,
        "rule_version": rule_version,
        "covered_entity_id": str(ctx.covered_entity_id),
        "investigation_case_id": None,
        "finding_type": rule_category,
        "severity": severity,
        "status": "open",
        "detected_at": now,
        "detection_method": "rules_engine",
        "confidence_score": str(_SEVERITY_CONFIDENCE.get(severity, Decimal("1.0"))),
        "financial_exposure": None,
        "financial_exposure_methodology": None,
        "purchase_id": str(ctx.purchase_id) if ctx.purchase_id else None,
        "purchase_date": ctx.purchase_date.isoformat() if ctx.purchase_date else None,
        "dispense_id": str(ctx.dispense_id) if ctx.dispense_id else None,
        "dispense_date": ctx.dispense_date.isoformat() if ctx.dispense_date else None,
        "claim_id": str(ctx.claim_id) if ctx.claim_id else None,
        "claim_service_date": ctx.claim_service_date.isoformat() if ctx.claim_service_date else None,
        "split_billing_id": str(ctx.split_billing_id),
        "evidence_payload": json.dumps(evidence),
        "entity_references": json.dumps(entity_refs),
        "violation_period_start": ctx.service_date.isoformat(),
        "violation_period_end": ctx.service_date.isoformat(),
        "resolved_at": None,
        "resolved_by": None,
        "resolution_type": None,
        "resolution_notes": None,
        "created_at": now,
        "updated_at": now,
    }
