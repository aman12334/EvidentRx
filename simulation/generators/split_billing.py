from __future__ import annotations

from decimal import Decimal
from uuid import uuid4
from typing import Optional


def build_split_billing(
    dispense: dict,
    claim: Optional[dict],
    inventory_balance: Decimal,
    cfg_source_tag: str,
    batch_id: str,
) -> dict:
    """
    Build a split_billing row linking purchase → dispense → claim.
    Pre-computes risk flags using deterministic rules (not AI).
    """
    is_340b_purchase = True   # all sim purchases are 340B
    is_medicaid = claim is not None and claim.get("is_medicaid", False)
    carve_out = dispense.get("carve_in_election") == "carve_out"

    # --- Deterministic risk flags ---

    # Duplicate discount: 340B purchase AND Medicaid claim with carve-out election violated
    duplicate_discount_risk = is_340b_purchase and is_medicaid

    # Medicaid overlap: 340B dispensed to Medicaid patient regardless of election
    medicaid_overlap_risk = is_340b_purchase and dispense.get("payer_type") == "medicaid"

    # Carve-out violation: CE has carve-out but still filed Medicaid claim on 340B drug
    carve_out_violation_risk = carve_out and is_medicaid and is_340b_purchase

    # Split billing mismatch: negative accumulator balance
    split_billing_mismatch = inventory_balance < Decimal("0")

    risk_score = _composite_risk(
        duplicate_discount_risk,
        medicaid_overlap_risk,
        carve_out_violation_risk,
        split_billing_mismatch,
    )

    service_date = dispense.get("dispense_date_raw") or dispense["dispense_date"]

    return {
        "split_billing_id": str(uuid4()),
        "covered_entity_id": dispense["covered_entity_id"],
        "ndc_11": dispense["ndc_11"],
        "service_date": str(service_date),
        "patient_id_hash": dispense["patient_id_hash"],
        # Logical links
        "purchase_id": dispense.get("_purchase_id"),
        "purchase_date": dispense.get("_purchase_date"),
        "dispense_id": dispense["dispense_id"],
        "dispense_date": dispense["dispense_date"],
        "claim_id": claim["claim_id"] if claim else None,
        "claim_service_date": str(claim["_service_date_raw"]) if claim else None,
        # Attributes
        "split_method": "accumulator",
        "carve_in_flag": carve_out,
        "is_340b_purchase": is_340b_purchase,
        "is_medicaid_billed": is_medicaid,
        "accumulator_balance": str(inventory_balance),
        # Risk flags
        "duplicate_discount_risk": duplicate_discount_risk,
        "medicaid_overlap_risk": medicaid_overlap_risk,
        "carve_out_violation_risk": carve_out_violation_risk,
        "ineligible_patient_risk": False,
        "risk_score": str(risk_score),
        "source_file": cfg_source_tag,
        "batch_id": batch_id,
        "created_at": "NOW()",
        "updated_at": "NOW()",
    }


def _composite_risk(*flags: bool) -> Decimal:
    """Simple weighted composite: each flag adds weight."""
    weights = [Decimal("0.40"), Decimal("0.25"), Decimal("0.25"), Decimal("0.10")]
    score = sum(w for flag, w in zip(flags, weights) if flag)
    return min(score, Decimal("1.0000"))
