"""Typed API contracts for evidence and lineage endpoints."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class PurchaseEvent(BaseModel):
    purchase_id:        UUID
    covered_entity_id:  UUID
    ndc_11:             str
    quantity:           float
    unit_cost:          float
    purchase_date:      date
    vendor_name:        str | None = None

    model_config = {"from_attributes": True}


class DispenseEvent(BaseModel):
    dispense_id:        UUID
    covered_entity_id:  UUID
    ndc_11:             str
    quantity:           float
    dispense_date:      date
    pharmacy_id:        UUID | None = None

    model_config = {"from_attributes": True}


class ClaimEvent(BaseModel):
    claim_id:           UUID
    covered_entity_id:  UUID
    ndc_11:             str
    claim_date:         date
    billed_amount:      float
    paid_amount:        float
    payer_type:         str | None = None

    model_config = {"from_attributes": True}


class EvidenceChain(BaseModel):
    """Full purchase → dispense → claim → finding lineage for a transaction."""
    finding_id:         UUID
    rule_code:          str
    severity:           str
    purchase:           PurchaseEvent | None = None
    dispense:           DispenseEvent | None = None
    claim:              ClaimEvent | None = None
    split_billing_id:   UUID | None = None
    pharmacy_name:      str | None = None
    pharmacy_id:        str | None = None
    ndc_11:             str | None = None
    notes:              str = ""


class EvidenceSnapshot(BaseModel):
    case_id:            UUID
    total_findings:     int
    chains:             list[EvidenceChain] = Field(default_factory=list)
    linked_pharmacies:  list[str] = Field(default_factory=list)
    linked_ndcs:        list[str] = Field(default_factory=list)
    linked_entities:    list[str] = Field(default_factory=list)
