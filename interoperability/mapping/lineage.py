"""
Transformation lineage tracking.

Records the full transformation chain for every canonical record:
  raw source → normalisation → validation → canonical → persistence

Lineage entries enable:
  - Compliance auditing (HRSA 340B requires 7-year audit trails)
  - Debugging failed normalisations
  - Replay: re-run normalisation on stored raw records
  - Data quality reporting (what % of records from each source are valid)

Lineage record structure
────────────────────────
  lineage_id       : UUID
  tenant_id        : Tenant
  source_system    : SourceSystem constant
  resource_type    : FHIR type / HL7 message type / EDI transaction type
  canonical_type   : CanonicalType constant
  checksum         : SHA-256 of the canonical record (for change detection)
  transformation_steps : list of {step, status, detail, timestamp}
  raw_ref          : Reference to raw storage (e.g. S3 key or DB row id)
  canonical_ref    : Reference to persisted canonical record
  created_at       : Timestamp of lineage record creation
  is_valid         : Whether the final canonical passed validation
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.interop.mapping.lineage")


# ── Step enums ────────────────────────────────────────────────────────────────

class LineageStep(str, Enum):
    INGEST        = "ingest"           # raw record received
    PARSE         = "parse"            # source-specific parsing (HL7, X12)
    NORMALISE     = "normalise"        # source → canonical mapping
    VALIDATE_RAW  = "validate_raw"     # structural validation before normalisation
    VALIDATE_CANON= "validate_canon"   # canonical validation after normalisation
    ENRICH        = "enrich"           # post-normalisation enrichment
    CHECKSUM      = "checksum"         # checksum computed
    PERSIST       = "persist"          # written to database
    DLQ           = "dlq"              # routed to dead-letter queue


class StepStatus(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    FAILED  = "failed"
    SKIPPED = "skipped"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class TransformationStep:
    step:      LineageStep
    status:    StepStatus
    detail:    str                         = ""
    timestamp: datetime                    = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step":      self.step.value,
            "status":    self.status.value,
            "detail":    self.detail,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class LineageRecord:
    lineage_id:           str
    tenant_id:            str
    source_system:        str
    resource_type:        str
    canonical_type:       Optional[str]
    checksum:             Optional[str]
    transformation_steps: list[TransformationStep]   = field(default_factory=list)
    raw_ref:              Optional[str]               = None    # S3 key or DB ref
    canonical_ref:        Optional[str]               = None    # DB row ID
    created_at:           datetime                    = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    is_valid:             bool                        = True
    error_summary:        Optional[str]               = None

    def add_step(
        self,
        step:   LineageStep,
        status: StepStatus,
        detail: str = "",
    ) -> None:
        self.transformation_steps.append(
            TransformationStep(step=step, status=status, detail=detail)
        )
        if status == StepStatus.FAILED:
            self.is_valid = False
            self.error_summary = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "lineage_id":            self.lineage_id,
            "tenant_id":             self.tenant_id,
            "source_system":         self.source_system,
            "resource_type":         self.resource_type,
            "canonical_type":        self.canonical_type,
            "checksum":              self.checksum,
            "transformation_steps":  [s.to_dict() for s in self.transformation_steps],
            "raw_ref":               self.raw_ref,
            "canonical_ref":         self.canonical_ref,
            "created_at":            self.created_at.isoformat(),
            "is_valid":              self.is_valid,
            "error_summary":         self.error_summary,
        }


# ── Lineage builder ───────────────────────────────────────────────────────────

class LineageBuilder:
    """
    Fluent builder for transformation lineage records.

    Usage
    ─────
      builder = LineageBuilder.start("fhir", "MedicationDispense", "t_001")
      builder.step(LineageStep.INGEST, StepStatus.SUCCESS)
      builder.step(LineageStep.NORMALISE, StepStatus.SUCCESS)
      builder.step(LineageStep.PERSIST, StepStatus.SUCCESS, canonical_ref="uuid-xyz")
      record = builder.build(canonical_type="dispense", checksum="abc123")
    """

    def __init__(
        self,
        source_system: str,
        resource_type: str,
        tenant_id:     str,
    ) -> None:
        self._lineage_id   = str(uuid.uuid4())
        self._source       = source_system
        self._resource     = resource_type
        self._tenant       = tenant_id
        self._steps:       list[TransformationStep] = []
        self._raw_ref:     Optional[str] = None
        self._canonical_ref: Optional[str] = None

    @classmethod
    def start(
        cls,
        source_system: str,
        resource_type: str,
        tenant_id:     str,
    ) -> "LineageBuilder":
        return cls(source_system, resource_type, tenant_id)

    def step(
        self,
        step:   LineageStep,
        status: StepStatus,
        detail: str = "",
    ) -> "LineageBuilder":
        self._steps.append(
            TransformationStep(step=step, status=status, detail=detail)
        )
        return self

    def with_raw_ref(self, ref: str) -> "LineageBuilder":
        self._raw_ref = ref
        return self

    def with_canonical_ref(self, ref: str) -> "LineageBuilder":
        self._canonical_ref = ref
        return self

    def build(
        self,
        canonical_type: Optional[str] = None,
        checksum:       Optional[str] = None,
    ) -> LineageRecord:
        failed = [s for s in self._steps if s.status == StepStatus.FAILED]
        return LineageRecord(
            lineage_id           = self._lineage_id,
            tenant_id            = self._tenant,
            source_system        = self._source,
            resource_type        = self._resource,
            canonical_type       = canonical_type,
            checksum             = checksum,
            transformation_steps = self._steps,
            raw_ref              = self._raw_ref,
            canonical_ref        = self._canonical_ref,
            is_valid             = len(failed) == 0,
            error_summary        = failed[-1].detail if failed else None,
        )


# ── Lineage store ─────────────────────────────────────────────────────────────

class LineageStore:
    """
    In-memory lineage store with optional async DB flush.

    Production implementation flushes to interop.source_lineage table.
    """

    def __init__(self, db_writer: Optional[Any] = None) -> None:
        self._buffer: list[LineageRecord] = []
        self._db_writer = db_writer

    def record(self, lineage: LineageRecord) -> None:
        """Add a lineage record to the buffer."""
        self._buffer.append(lineage)
        log.debug(
            "Lineage [%s]: %s/%s → %s valid=%s",
            lineage.lineage_id[:8],
            lineage.source_system,
            lineage.resource_type,
            lineage.canonical_type,
            lineage.is_valid,
        )

    async def flush(self) -> int:
        """Flush buffered lineage records to the database."""
        if not self._buffer:
            return 0
        batch = list(self._buffer)
        self._buffer.clear()

        if self._db_writer is None:
            log.debug("LineageStore: no DB writer — %d records discarded", len(batch))
            return len(batch)

        try:
            await self._db_writer(batch)
            log.info("LineageStore: flushed %d records", len(batch))
            return len(batch)
        except Exception as exc:
            log.error("LineageStore: flush failed: %s", exc)
            # Put records back in buffer for next attempt
            self._buffer[:0] = batch
            return 0

    def size(self) -> int:
        return len(self._buffer)


# ── Module-level store singleton ──────────────────────────────────────────────

_store: Optional[LineageStore] = None


def get_lineage_store(db_writer: Optional[Any] = None) -> LineageStore:
    """Return the module-level LineageStore singleton."""
    global _store
    if _store is None:
        _store = LineageStore(db_writer=db_writer)
    return _store
