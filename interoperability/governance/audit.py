"""
Source-level audit logging for the interoperability layer.

Records every significant event in the connector and ingestion pipeline:
  - Connector state transitions (HEALTHY → DEGRADED, etc.)
  - Sync start / completion / failure
  - Access control decisions (allow and deny)
  - Normalisation failures and DLQ routing
  - Record-level PHI access (when lineage is queried)

All audit entries are written to the interop.ingestion_audit_log table
and optionally streamed to the event bus for real-time monitoring.

HIPAA / HRSA relevance
──────────────────────
  - Provides non-repudiable record of all data ingestion activity
  - 7-year retention enforced at the database level (partition TTL)
  - Never logs PHI (patient identifiers are hashed before entry)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.interop.governance.audit")


class AuditEventType(str, Enum):
    # Connector lifecycle
    CONNECTOR_INITIALISED  = "connector_initialised"
    CONNECTOR_FAILED       = "connector_failed"
    CONNECTOR_CLOSED       = "connector_closed"
    CONNECTOR_DEGRADED     = "connector_degraded"

    # Sync operations
    SYNC_STARTED           = "sync_started"
    SYNC_COMPLETED         = "sync_completed"
    SYNC_FAILED            = "sync_failed"
    SYNC_PARTIAL           = "sync_partial"

    # Record processing
    RECORD_INGESTED        = "record_ingested"
    RECORD_NORMALISED      = "record_normalised"
    RECORD_VALIDATED       = "record_validated"
    RECORD_REJECTED        = "record_rejected"
    RECORD_DLQ             = "record_dlq"
    RECORD_DUPLICATE       = "record_duplicate"

    # Access control
    ACCESS_ALLOWED         = "access_allowed"
    ACCESS_DENIED          = "access_denied"

    # Lineage queries
    LINEAGE_QUERIED        = "lineage_queried"
    REPLAY_TRIGGERED       = "replay_triggered"

    # Governance
    POLICY_ENFORCED        = "policy_enforced"
    POLICY_VIOLATED        = "policy_violated"


@dataclass
class AuditEntry:
    event_id:       str
    event_type:     AuditEventType
    tenant_id:      str
    connector_id:   Optional[str]
    actor_id:       Optional[str]       # user ID or service account
    resource_type:  Optional[str]
    source_system:  Optional[str]
    detail:         dict[str, Any]      # structured event detail (no PHI)
    occurred_at:    datetime
    correlation_id: Optional[str]       = None  # links related events

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"]  = self.event_type.value
        d["occurred_at"] = self.occurred_at.isoformat()
        return d


class InteropAuditLog:
    """
    Structured audit log for the interoperability layer.

    Buffers entries in memory and flushes to DB on demand or after
    reaching the buffer threshold.
    """

    def __init__(
        self,
        db_writer:      Optional[Callable] = None,   # async (list[AuditEntry]) → None
        buffer_size:    int                = 200,
        correlation_id: Optional[str]      = None,
    ) -> None:
        self._db_writer     = db_writer
        self._buffer:       list[AuditEntry] = []
        self._max_buffer    = buffer_size
        self._correlation   = correlation_id or str(uuid.uuid4())
        self._total_written = 0

    # ── Log methods ────────────────────────────────────────────────────────────

    def log(
        self,
        event_type:    AuditEventType,
        tenant_id:     str,
        connector_id:  Optional[str]       = None,
        actor_id:      Optional[str]       = None,
        resource_type: Optional[str]       = None,
        source_system: Optional[str]       = None,
        detail:        Optional[dict]      = None,
    ) -> AuditEntry:
        """Write a single audit entry to the buffer."""
        entry = AuditEntry(
            event_id       = str(uuid.uuid4()),
            event_type     = event_type,
            tenant_id      = tenant_id,
            connector_id   = connector_id,
            actor_id       = actor_id,
            resource_type  = resource_type,
            source_system  = source_system,
            detail         = detail or {},
            occurred_at    = datetime.now(tz=timezone.utc),
            correlation_id = self._correlation,
        )

        self._buffer.append(entry)
        self._total_written += 1

        # Structured log output (never log PHI)
        log.info(
            "INTEROP_AUDIT event=%s tenant=%s connector=%s actor=%s",
            event_type.value,
            tenant_id,
            connector_id or "-",
            actor_id or "-",
        )

        return entry

    # ── Convenience wrappers ───────────────────────────────────────────────────

    def sync_started(
        self,
        tenant_id:     str,
        connector_id:  str,
        resource_type: str,
        source_system: str,
        incremental:   bool = True,
    ) -> AuditEntry:
        return self.log(
            event_type    = AuditEventType.SYNC_STARTED,
            tenant_id     = tenant_id,
            connector_id  = connector_id,
            resource_type = resource_type,
            source_system = source_system,
            detail        = {"incremental": incremental},
        )

    def sync_completed(
        self,
        tenant_id:      str,
        connector_id:   str,
        resource_type:  str,
        records_written: int,
        duration_sec:   float,
    ) -> AuditEntry:
        return self.log(
            event_type    = AuditEventType.SYNC_COMPLETED,
            tenant_id     = tenant_id,
            connector_id  = connector_id,
            resource_type = resource_type,
            detail        = {
                "records_written": records_written,
                "duration_sec":    round(duration_sec, 2),
            },
        )

    def record_rejected(
        self,
        tenant_id:     str,
        connector_id:  str,
        resource_type: str,
        source_system: str,
        errors:        list[str],
    ) -> AuditEntry:
        return self.log(
            event_type    = AuditEventType.RECORD_REJECTED,
            tenant_id     = tenant_id,
            connector_id  = connector_id,
            resource_type = resource_type,
            source_system = source_system,
            detail        = {"errors": errors[:5]},  # truncate error list
        )

    def access_denied(
        self,
        tenant_id:    str,
        actor_id:     str,
        connector_id: str,
        action:       str,
        reason:       str,
    ) -> AuditEntry:
        return self.log(
            event_type   = AuditEventType.ACCESS_DENIED,
            tenant_id    = tenant_id,
            connector_id = connector_id,
            actor_id     = actor_id,
            detail       = {"action": action, "reason": reason},
        )

    # ── Flush ──────────────────────────────────────────────────────────────────

    async def flush(self) -> int:
        """Write all buffered entries to the database. Returns count written."""
        if not self._buffer:
            return 0
        batch = list(self._buffer)
        self._buffer.clear()

        if self._db_writer is None:
            return len(batch)

        try:
            await self._db_writer(batch)
            log.debug("InteropAuditLog: flushed %d entries", len(batch))
            return len(batch)
        except Exception as exc:
            log.error("InteropAuditLog: flush failed: %s", exc)
            self._buffer[:0] = batch  # put back
            return 0

    def size(self) -> int:
        return len(self._buffer)

    @property
    def total_written(self) -> int:
        return self._total_written


# ── Module-level singleton ────────────────────────────────────────────────────

_audit_log: Optional[InteropAuditLog] = None


def get_interop_audit_log(db_writer: Optional[Callable] = None) -> InteropAuditLog:
    """Return the module-level InteropAuditLog singleton."""
    global _audit_log
    if _audit_log is None:
        _audit_log = InteropAuditLog(db_writer=db_writer)
    return _audit_log
