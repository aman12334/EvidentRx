"""
Rule evaluation context — carries a split_billing row and all joined reference
data needed for a single rule evaluation pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID


@dataclass
class RuleContext:
    # Core split billing record
    split_billing_id: UUID
    covered_entity_id: UUID
    ndc_11: str
    service_date: date
    patient_id_hash: Optional[str]

    # Logical chain IDs
    purchase_id: Optional[UUID]
    purchase_date: Optional[date]
    dispense_id: Optional[UUID]
    dispense_date: Optional[date]
    claim_id: Optional[UUID]
    claim_service_date: Optional[date]

    # Split billing attributes
    is_340b_purchase: bool
    is_medicaid_billed: bool
    carve_in_flag: Optional[bool]
    accumulator_balance: Optional[Decimal]

    # Pre-computed risk signals (from simulation/ingestion)
    duplicate_discount_risk: bool
    medicaid_overlap_risk: bool
    carve_out_violation_risk: bool
    ineligible_patient_risk: bool

    # Reference data joined at query time
    ce_program_start: Optional[date] = None
    ce_program_end: Optional[date] = None
    cp_registered: Optional[bool] = None         # True if CP was active at service_date
    cp_termination_date: Optional[date] = None
    has_carve_out_election: Optional[bool] = None

    # Extra context fields for evidence payloads
    extra: dict = field(default_factory=dict)
