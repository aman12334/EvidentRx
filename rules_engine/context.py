"""
Rule evaluation context — carries a split_billing row and all joined reference
data needed for a single rule evaluation pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass
class RuleContext:
    # Core split billing record
    split_billing_id: UUID
    covered_entity_id: UUID
    ndc_11: str
    service_date: date
    patient_id_hash: str | None

    # Logical chain IDs
    purchase_id: UUID | None
    purchase_date: date | None
    dispense_id: UUID | None
    dispense_date: date | None
    claim_id: UUID | None
    claim_service_date: date | None

    # Split billing attributes
    is_340b_purchase: bool
    is_medicaid_billed: bool
    carve_in_flag: bool | None
    accumulator_balance: Decimal | None

    # Pre-computed risk signals (from simulation/ingestion)
    duplicate_discount_risk: bool
    medicaid_overlap_risk: bool
    carve_out_violation_risk: bool
    ineligible_patient_risk: bool

    # Reference data joined at query time
    ce_program_start: date | None = None
    ce_program_end: date | None = None
    cp_registered: bool | None = None         # True if CP was active at service_date
    cp_termination_date: date | None = None
    has_carve_out_election: bool | None = None

    # Extra context fields for evidence payloads
    extra: dict = field(default_factory=dict)
