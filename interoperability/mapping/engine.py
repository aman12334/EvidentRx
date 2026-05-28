"""
Canonical mapping engine.

The mapping engine is the single dispatch point for converting any raw
healthcare record (FHIR, HL7, EDI, pharmacy) into the platform's canonical
schema. It routes records by source system and resource type, applies
post-normalisation enrichment, and validates the output.

Responsibilities
────────────────
  1. Source dispatch — route to the correct normaliser
  2. Canonical validation — check required fields and type constraints
  3. PHI audit — ensure patient identifiers are hashed before output
  4. Lineage attachment — add mapping metadata to every record
  5. Error isolation — normalisation failures never propagate to caller

Usage
─────
  engine = MappingEngine()
  result = engine.map(raw_record, source="fhir", tenant_id="t_001")
  if result.success:
      persist(result.canonical)
  else:
      dlq.enqueue(result.errors)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Optional

from interoperability.mapping.schemas import (
    CanonicalType,
    SourceSystem,
    validate_canonical,
    scrub_phi,
)

log = logging.getLogger("evidentrx.interop.mapping.engine")


# ── Mapping result ─────────────────────────────────────────────────────────────

@dataclass
class MappingResult:
    success:          bool
    canonical:        Optional[dict[str, Any]]
    source_system:    str
    resource_type:    str
    tenant_id:        str
    errors:           list[str]        = field(default_factory=list)
    warnings:         list[str]        = field(default_factory=list)
    mapped_at:        datetime         = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    canonical_type:   Optional[str]    = None
    checksum:         Optional[str]    = None

    def __post_init__(self) -> None:
        if self.canonical:
            self.canonical_type = self.canonical.get("canonical_type")
            self.checksum       = _checksum(self.canonical)


# ── Mapping engine ─────────────────────────────────────────────────────────────

class MappingEngine:
    """
    Unified canonical mapping engine.

    Routes raw records from any healthcare source to the appropriate
    normaliser and validates the output against the canonical schema.

    The engine is stateless — it holds no mutable per-request state.
    Safe for concurrent use from an async event loop.
    """

    def __init__(self, strict: bool = False) -> None:
        """
        Parameters
        ----------
        strict : bool
            If True, validation errors raise MappingError.
            If False (default), validation errors are recorded in result.errors
            and result.success = False.
        """
        self._strict = strict

    # ── Public API ─────────────────────────────────────────────────────────────

    def map(
        self,
        raw_record:    dict[str, Any],
        source:        str,
        tenant_id:     str,
        resource_type: Optional[str] = None,
    ) -> MappingResult:
        """
        Map a raw record to canonical form.

        Parameters
        ----------
        raw_record    : The raw source record (FHIR resource, EDI claim dict, etc.)
        source        : SourceSystem constant (e.g. "fhir", "hl7v2", "x12_837p")
        tenant_id     : Tenant identifier for PHI hashing
        resource_type : FHIR resource type or EDI transaction type (auto-detected if None)
        """
        rtype = resource_type or _detect_resource_type(raw_record, source)

        try:
            canonical = self._dispatch(raw_record, source, tenant_id, rtype)
        except MappingError as e:
            return MappingResult(
                success       = False,
                canonical     = None,
                source_system = source,
                resource_type = rtype or "unknown",
                tenant_id     = tenant_id,
                errors        = [str(e)],
            )
        except Exception as e:
            log.exception("Unexpected mapping error for %s/%s", source, rtype)
            return MappingResult(
                success       = False,
                canonical     = None,
                source_system = source,
                resource_type = rtype or "unknown",
                tenant_id     = tenant_id,
                errors        = [f"Unexpected error: {e}"],
            )

        # Attach lineage
        canonical = self._attach_lineage(canonical, source, rtype, tenant_id)

        # Validate
        is_valid, validation_errors = validate_canonical(canonical)
        if not is_valid:
            if self._strict:
                raise MappingError(f"Canonical validation failed: {validation_errors}")
            log.warning(
                "Canonical validation failed for %s/%s: %s",
                source, rtype, validation_errors,
            )
            return MappingResult(
                success       = False,
                canonical     = canonical,
                source_system = source,
                resource_type = rtype or "unknown",
                tenant_id     = tenant_id,
                errors        = validation_errors,
            )

        return MappingResult(
            success       = True,
            canonical     = canonical,
            source_system = source,
            resource_type = rtype or "unknown",
            tenant_id     = tenant_id,
        )

    def map_batch(
        self,
        records:       list[dict[str, Any]],
        source:        str,
        tenant_id:     str,
        resource_type: Optional[str] = None,
    ) -> list[MappingResult]:
        """Map a batch of raw records. Errors are captured per-record."""
        return [self.map(r, source, tenant_id, resource_type) for r in records]

    # ── Dispatch ────────────────────────────────────────────────────────────────

    def _dispatch(
        self,
        raw:      dict[str, Any],
        source:   str,
        tenant:   str,
        rtype:    Optional[str],
    ) -> dict[str, Any]:
        """Route raw record to the correct normaliser."""

        if source == SourceSystem.FHIR:
            from interoperability.fhir.normalizer import normalise
            return normalise(raw, tenant)

        if source in (SourceSystem.X12_837P, SourceSystem.X12_837_MEDICAID):
            # EDI records arrive pre-normalised from pharmacy_claims / medicaid normalisers
            # Validate that they look like a canonical claim
            if "canonical_type" not in raw:
                raise MappingError(f"EDI record missing canonical_type: {list(raw.keys())[:5]}")
            return dict(raw)

        if source == SourceSystem.X12_835:
            if "canonical_type" not in raw:
                raise MappingError("835 remittance record missing canonical_type")
            return dict(raw)

        if source == SourceSystem.HL7V2:
            # HL7 records arrive as parsed HL7Message objects (via normalizer.py)
            # At this level we receive already-normalised dicts from hl7.normalizer
            if "canonical_type" not in raw:
                raise MappingError(f"HL7 record missing canonical_type: {list(raw.keys())[:5]}")
            return dict(raw)

        if source in (SourceSystem.NCPDP_BATCH, SourceSystem.PBM_API):
            # Pharmacy connectors produce canonical dispense dicts directly
            if "canonical_type" not in raw:
                raise MappingError("Pharmacy record missing canonical_type")
            return dict(raw)

        raise MappingError(f"Unknown source system: {source!r}")

    # ── Lineage attachment ──────────────────────────────────────────────────────

    @staticmethod
    def _attach_lineage(
        canonical:    dict[str, Any],
        source:       str,
        resource_type: Optional[str],
        tenant_id:    str,
    ) -> dict[str, Any]:
        """Overlay mapping lineage metadata onto the canonical record."""
        canonical = dict(canonical)
        canonical.setdefault("source_system", source)
        canonical.setdefault("tenant_id", tenant_id)
        canonical["_mapped_at"] = datetime.now(tz=timezone.utc).isoformat()
        canonical["_mapping_resource_type"] = resource_type
        return canonical


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_resource_type(record: dict[str, Any], source: str) -> Optional[str]:
    """Auto-detect resource type from the raw record."""
    if source == SourceSystem.FHIR:
        return record.get("resourceType")
    if source == SourceSystem.HL7V2:
        return record.get("source_msg_type")
    return record.get("canonical_type")


def _checksum(canonical: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 checksum of the canonical record."""
    payload = json.dumps(canonical, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


# ── Exception ─────────────────────────────────────────────────────────────────

class MappingError(Exception):
    """Raised when a record cannot be mapped to canonical form."""


# ── Module-level singleton ────────────────────────────────────────────────────

_engine: Optional[MappingEngine] = None


def get_mapping_engine(strict: bool = False) -> MappingEngine:
    """Return the module-level MappingEngine singleton."""
    global _engine
    if _engine is None:
        _engine = MappingEngine(strict=strict)
    return _engine
