"""Typed API contracts for audit finding endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class FindingSchema(BaseModel):
    finding_id:         UUID
    case_id:            Optional[UUID] = None
    finding_code:       str
    rule_code:          str
    severity:           str
    covered_entity_id:  UUID
    entity_name:        Optional[str] = None
    evidence_payload:   dict[str, Any] = Field(default_factory=dict)
    created_at:         Optional[datetime] = None

    model_config = {"from_attributes": True}


class FindingDetail(FindingSchema):
    entity_references:  dict[str, Any] = Field(default_factory=dict)
    ndc_11:             Optional[str] = None
    pharmacy_id:        Optional[str] = None
    pharmacy_name:      Optional[str] = None
    financial_exposure: float = 0.0


class FindingListResponse(BaseModel):
    total:  int
    page:   int
    limit:  int
    items:  list[FindingSchema]


class FindingsByRule(BaseModel):
    rule_code:   str
    count:       int
    critical:    int = 0
    high:        int = 0
    medium:      int = 0
    low:         int = 0
    exposure:    float = 0.0
