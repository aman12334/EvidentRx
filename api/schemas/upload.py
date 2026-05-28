"""
Pydantic schemas for the data upload pipeline.

These are the canonical type definitions used by both the upload router
and any future consumer (e.g. webhook callbacks, async result polling).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FindingSummary(BaseModel):
    """Per-rule finding count from an upload batch."""
    rule_code:   str = Field(..., description="Rule code, e.g. DD-001")
    description: str = Field(..., description="Human-readable rule name")
    count:       int = Field(..., ge=0, description="Number of findings for this rule")
    severity:    str = Field(..., description="critical | high | medium | low")


class UploadResult(BaseModel):
    """Full pipeline result returned after a successful upload."""
    upload_id:          str   = Field(..., description="Unique ID for this upload request")
    batch_id:           str   = Field(..., description="DB batch_id for data lineage")
    status:             str   = Field(..., description="complete | partial | no_findings")
    message:            str   = Field(..., description="Human-readable summary")
    rows_parsed:        int   = Field(..., ge=0)
    dispenses_inserted: int   = Field(..., ge=0)
    claims_inserted:    int   = Field(..., ge=0)
    split_billing_rows: int   = Field(..., ge=0)
    cases_created:      int   = Field(..., ge=0)
    total_findings:     int   = Field(..., ge=0)
    critical_findings:  int   = Field(..., ge=0)
    high_findings:      int   = Field(..., ge=0)
    estimated_exposure: float | None = Field(None, description="Total financial exposure in USD")
    findings_by_rule:   list[FindingSummary] = Field(default_factory=list)
    case_ids:           list[str]            = Field(default_factory=list)
    processing_ms:      int   = Field(..., ge=0, description="End-to-end processing time in ms")


class BatchHistoryItem(BaseModel):
    """One row from the upload batch history table."""
    batch_id:       str           = Field(..., description="UUID of the ingestion batch")
    filename:       str           = Field(..., description="Original uploaded filename")
    status:         str           = Field(..., description="processing | complete | failed")
    record_count:   int           = Field(..., ge=0)
    started_at:     str           = Field(..., description="ISO-8601 UTC timestamp")
    completed_at:   str | None = Field(None)
    findings_count: int | None = Field(None, ge=0)


class UploadError(BaseModel):
    """Structured error response for failed uploads."""
    error_code: str
    detail:     str
    row_number: int | None = None
    column:     str | None = None


class ColumnMapping(BaseModel):
    """Describes how an uploaded CSV column maps to the internal schema."""
    source_column:  str
    target_field:   str
    mapped:         bool
    sample_value:   str | None = None


class UploadPreview(BaseModel):
    """
    Dry-run result — shows column mappings and row count before committing.
    Call POST /upload/preview (future endpoint) to inspect a file without
    running the full compliance pipeline.
    """
    filename:        str
    rows_detected:   int
    file_type:       str             = Field(..., description="dispenses | claims | mixed")
    column_mappings: list[ColumnMapping]
    unmapped_columns: list[str]      = Field(default_factory=list)
    sample_rows:     list[dict]      = Field(default_factory=list)
    warnings:        list[str]       = Field(default_factory=list)
