"""
Regulatory document models for the ingestion layer.

Every piece of regulatory guidance — HRSA notices, CMS policy updates,
Medicaid bulletins, payer communications — enters the system as a
RegulatoryDocument. Documents are versioned, source-attributed, and
content-hashed for tamper detection.

Document lifecycle
──────────────────
  PENDING  → ingestion queued but not yet processed
  PARSING  → parser running (PDF / HTML / structured feed)
  PARSED   → metadata extracted; awaiting indexing
  INDEXED  → available for diff and graph operations
  ARCHIVED → superseded by a newer version (kept for replay)
  FAILED   → parsing failed; error recorded; retryable
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional


class DocumentSource(str, Enum):
    HRSA               = "hrsa"
    CMS                = "cms"
    MEDICAID_STATE     = "medicaid_state"
    PAYER_BULLETIN     = "payer_bulletin"
    FEDERAL_REGISTER   = "federal_register"
    PHARMACY_GUIDANCE  = "pharmacy_guidance"
    OPERATIONAL_POLICY = "operational_policy"
    INTERNAL_POLICY    = "internal_policy"


class DocumentFormat(str, Enum):
    PDF        = "pdf"
    HTML       = "html"
    JSON_FEED  = "json_feed"
    XML_FEED   = "xml_feed"
    PLAIN_TEXT = "plain_text"


class DocumentStatus(str, Enum):
    PENDING  = "pending"
    PARSING  = "parsing"
    PARSED   = "parsed"
    INDEXED  = "indexed"
    ARCHIVED = "archived"
    FAILED   = "failed"


class PolicyDomain(str, Enum):
    DRUG_340B           = "drug_340b"
    MEDICAID_CARVE_IN   = "medicaid_carve_in"
    MEDICAID_CARVE_OUT  = "medicaid_carve_out"
    CONTRACT_PHARMACY   = "contract_pharmacy"
    AUDIT_REQUIREMENTS  = "audit_requirements"
    BILLING_COMPLIANCE  = "billing_compliance"
    COVERED_ENTITY_ELIG = "covered_entity_eligibility"
    DISPUTE_RESOLUTION  = "dispute_resolution"
    PRICING_INTEGRITY   = "pricing_integrity"
    GENERAL_COMPLIANCE  = "general_compliance"


@dataclass
class PolicySourceAttribution:
    """Traceable attribution for a regulatory document."""
    source:          DocumentSource
    issuing_agency:  str                  # e.g. "HRSA Office of Pharmacy Affairs"
    document_number: Optional[str]        # docket, notice, or guidance number
    publication_url: Optional[str]
    publication_date: Optional[str]       # ISO-8601 date
    effective_date:  Optional[str]        # ISO-8601 date
    expiry_date:     Optional[str]        # ISO-8601 date (None = indefinite)
    jurisdiction:    str                  = "federal"   # state code or "federal"
    payer_id:        Optional[str]        = None        # for payer bulletins

    def to_dict(self) -> dict[str, Any]:
        return {
            "source":           self.source.value,
            "issuing_agency":   self.issuing_agency,
            "document_number":  self.document_number,
            "publication_url":  self.publication_url,
            "publication_date": self.publication_date,
            "effective_date":   self.effective_date,
            "expiry_date":      self.expiry_date,
            "jurisdiction":     self.jurisdiction,
        }


@dataclass
class RegulatoryDocument:
    """
    A versioned regulatory document ingested into the platform.

    content_hash is SHA-256 of the raw document bytes, computed at
    ingest time and verified on each retrieval to detect tampering.
    Consecutive versions of the same document share the same
    document_family_id.
    """
    doc_id:              str
    document_family_id:  str          # groups all versions of the same regulation
    title:               str
    version:             str
    status:              DocumentStatus
    fmt:                 DocumentFormat
    attribution:         PolicySourceAttribution
    domains:             list[PolicyDomain]
    content_hash:        str          # SHA-256 of raw bytes
    raw_text:            str          = ""    # extracted plain-text content
    summary:             str          = ""    # human-readable summary (extracted or generated)
    key_changes:         list[str]    = field(default_factory=list)   # bullet-point change list
    prior_version_id:    Optional[str] = None
    ingested_at:         datetime     = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    indexed_at:          Optional[datetime] = None
    error:               Optional[str] = None
    tags:                list[str]    = field(default_factory=list)
    metadata:            dict[str, Any] = field(default_factory=dict)

    @property
    def is_current(self) -> bool:
        return self.status == DocumentStatus.INDEXED

    @property
    def effective_date(self) -> Optional[str]:
        return self.attribution.effective_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id":             self.doc_id,
            "document_family_id": self.document_family_id,
            "title":              self.title,
            "version":            self.version,
            "status":             self.status.value,
            "format":             self.fmt.value,
            "attribution":        self.attribution.to_dict(),
            "domains":            [d.value for d in self.domains],
            "content_hash":       self.content_hash,
            "summary":            self.summary,
            "key_changes":        self.key_changes,
            "prior_version_id":   self.prior_version_id,
            "ingested_at":        self.ingested_at.isoformat(),
            "indexed_at":         self.indexed_at.isoformat() if self.indexed_at else None,
            "tags":               self.tags,
        }


@dataclass
class IngestionRecord:
    """
    Audit record for a single document ingestion attempt.

    Immutable once written. Retries create new IngestionRecords.
    """
    record_id:     str
    doc_id:        str
    source:        DocumentSource
    source_url:    Optional[str]
    triggered_by:  str          # "scheduled_sync" | "manual" | "webhook"
    started_at:    datetime
    completed_at:  Optional[datetime] = None
    success:       bool               = False
    bytes_fetched: int                = 0
    parse_errors:  list[str]          = field(default_factory=list)
    metadata:      dict[str, Any]     = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id":    self.record_id,
            "doc_id":       self.doc_id,
            "source":       self.source.value,
            "triggered_by": self.triggered_by,
            "started_at":   self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "success":      self.success,
            "bytes_fetched":self.bytes_fetched,
        }


def _hash_content(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def new_doc_id() -> str:
    return f"rdoc_{uuid.uuid4().hex[:16]}"


def new_family_id() -> str:
    return f"rfam_{uuid.uuid4().hex[:16]}"
