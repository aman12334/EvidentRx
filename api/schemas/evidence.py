"""Typed API contracts for evidence and lineage endpoints."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PurchaseEvent(BaseModel):
    purchase_id:        UUID
    covered_entity_id:  UUID
    ndc_11:             str
    quantity:           float
    unit_cost:          float
    purchase_date:      date
    vendor_name:        Optional[str] = None

    model_config = {"from_attributes": True}


class DispenseEvent(BaseModel):
    dispense_id:        UUID
    covered_entity_id:  UUID
    ndc_11:             str
    quantity:           float
    dispense_date:      date
    pharmacy_id:        Optional[UUID] = None

    model_config = {"from_attributes": True}


class ClaimEvent(BaseModel):
    claim_id:           UUID
    covered_entity_id:  UUID
    ndc_11:             str
    claim_date:         date
    billed_amount:      float
    paid_amount:        float
    payer_type:         Optional[str] = None

    model_config = {"from_attributes": True}


class EvidenceChain(BaseModel):
    """Full purchase → dispense → claim → finding lineage for a transaction."""
    finding_id:         UUID
    rule_code:          str
    severity:           str
    purchase:           Optional[PurchaseEvent] = None
    dispense:           Optional[DispenseEvent] = None
    claim:              Optional[ClaimEvent] = None
    split_billing_id:   Optional[UUID] = None
    pharmacy_name:      Optional[str] = None
    pharmacy_id:        Optional[str] = None
    ndc_11:             Optional[str] = None
    notes:              str = ""


class EvidenceSnapshot(BaseModel):
    case_id:            UUID
    total_findings:     int
    chains:             list[EvidenceChain] = Field(default_factory=list)
    linked_pharmacies:  list[str] = Field(default_factory=list)
    linked_ndcs:        list[str] = Field(default_factory=list)
    linked_entities:    list[str] = Field(default_factory=list)
