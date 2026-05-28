"""
SaaS usage metering.

Records every billable usage event for tenant-level accounting. All
usage signals flow through the UsageMeter before being aggregated by
UsageAccounting into billing periods.

Metered resources
─────────────────
  INVESTIGATION_RUN      — one complete investigation execution
  MODEL_TOKENS_IN        — LLM input tokens (per 1K)
  MODEL_TOKENS_OUT       — LLM output tokens (per 1K)
  RULE_EVALUATION        — one rule engine evaluation pass
  INGESTION_RECORD       — one canonical record ingested via interop
  API_REQUEST            — one authenticated API call
  STORAGE_GB_DAY         — GB·day of investigation + evidence storage
  WORKFLOW_EXECUTION     — one workflow (LangGraph) execution
  EXPORT_RECORD          — one record exported (bulk export or report)
  ANALYST_SEAT_DAY       — one active analyst seat·day
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, date, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.saas.billing.meter")


class UsageEventType(str, Enum):
    INVESTIGATION_RUN  = "investigation_run"
    MODEL_TOKENS_IN    = "model_tokens_in"
    MODEL_TOKENS_OUT   = "model_tokens_out"
    RULE_EVALUATION    = "rule_evaluation"
    INGESTION_RECORD   = "ingestion_record"
    API_REQUEST        = "api_request"
    STORAGE_GB_DAY     = "storage_gb_day"
    WORKFLOW_EXECUTION = "workflow_execution"
    EXPORT_RECORD      = "export_record"
    ANALYST_SEAT_DAY   = "analyst_seat_day"


@dataclass
class UsageEvent:
    """
    A single billable usage event.

    Immutable once written. Carries the tenant, resource type, quantity,
    and enough context for downstream cost attribution (org, entity).
    """
    event_id:   str
    tenant_id:  str
    event_type: UsageEventType
    quantity:   float               # in natural units for the event type
    unit:       str                 # "runs" | "tokens" | "records" | "gb_days" | …
    occurred_at: datetime
    org_id:     Optional[str]       = None
    entity_id:  Optional[str]       = None   # covered_entity_id
    model_id:   Optional[str]       = None   # for token events
    metadata:   dict[str, Any]      = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":   self.event_id,
            "tenant_id":  self.tenant_id,
            "event_type": self.event_type.value,
            "quantity":   self.quantity,
            "unit":       self.unit,
            "occurred_at":self.occurred_at.isoformat(),
            "org_id":     self.org_id,
            "entity_id":  self.entity_id,
        }


# Natural unit labels for each event type
_UNITS: dict[UsageEventType, str] = {
    UsageEventType.INVESTIGATION_RUN:  "runs",
    UsageEventType.MODEL_TOKENS_IN:    "1k_tokens",
    UsageEventType.MODEL_TOKENS_OUT:   "1k_tokens",
    UsageEventType.RULE_EVALUATION:    "evaluations",
    UsageEventType.INGESTION_RECORD:   "records",
    UsageEventType.API_REQUEST:        "requests",
    UsageEventType.STORAGE_GB_DAY:     "gb_days",
    UsageEventType.WORKFLOW_EXECUTION: "executions",
    UsageEventType.EXPORT_RECORD:      "records",
    UsageEventType.ANALYST_SEAT_DAY:   "seat_days",
}


class UsageMeter:
    """
    Writes usage events and buffers them for periodic persistence.

    The meter is the write path — high-throughput, low-overhead. It
    buffers events in memory and flushes to the DB writer in batches.
    """

    def __init__(
        self,
        db_writer:      Optional[Callable] = None,
        flush_size:     int                = 200,
    ) -> None:
        self._buffer:    list[UsageEvent] = []
        self._db_writer  = db_writer
        self._flush_size = flush_size
        # Lightweight in-memory totals for health checks
        self._totals:    dict[tuple[str, str], float] = {}   # (tenant_id, event_type) → total

    # ── Record ─────────────────────────────────────────────────────────────────

    async def record(
        self,
        tenant_id:  str,
        event_type: UsageEventType,
        quantity:   float            = 1.0,
        org_id:     Optional[str]   = None,
        entity_id:  Optional[str]   = None,
        model_id:   Optional[str]   = None,
        metadata:   Optional[dict]  = None,
    ) -> UsageEvent:
        event = UsageEvent(
            event_id    = str(uuid.uuid4()),
            tenant_id   = tenant_id,
            event_type  = event_type,
            quantity    = quantity,
            unit        = _UNITS.get(event_type, "units"),
            occurred_at = datetime.now(tz=timezone.utc),
            org_id      = org_id,
            entity_id   = entity_id,
            model_id    = model_id,
            metadata    = metadata or {},
        )
        self._buffer.append(event)
        key = (tenant_id, event_type.value)
        self._totals[key] = self._totals.get(key, 0.0) + quantity

        if len(self._buffer) >= self._flush_size:
            await self.flush()

        return event

    # ── Convenience recorders ──────────────────────────────────────────────────

    async def record_investigation(
        self,
        tenant_id: str,
        org_id:    Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> UsageEvent:
        return await self.record(tenant_id, UsageEventType.INVESTIGATION_RUN,
                                 org_id=org_id, entity_id=entity_id)

    async def record_tokens(
        self,
        tenant_id:  str,
        tokens_in:  int,
        tokens_out: int,
        model_id:   str,
        org_id:     Optional[str] = None,
    ) -> None:
        if tokens_in > 0:
            await self.record(
                tenant_id, UsageEventType.MODEL_TOKENS_IN,
                quantity  = tokens_in / 1000,
                model_id  = model_id,
                org_id    = org_id,
            )
        if tokens_out > 0:
            await self.record(
                tenant_id, UsageEventType.MODEL_TOKENS_OUT,
                quantity  = tokens_out / 1000,
                model_id  = model_id,
                org_id    = org_id,
            )

    async def record_api_request(
        self,
        tenant_id: str,
        endpoint:  str,
        org_id:    Optional[str] = None,
    ) -> UsageEvent:
        return await self.record(
            tenant_id, UsageEventType.API_REQUEST,
            metadata  = {"endpoint": endpoint},
            org_id    = org_id,
        )

    async def record_ingestion(
        self,
        tenant_id:  str,
        record_count: int,
        source:     str,
        org_id:     Optional[str] = None,
    ) -> UsageEvent:
        return await self.record(
            tenant_id, UsageEventType.INGESTION_RECORD,
            quantity  = record_count,
            metadata  = {"source": source},
            org_id    = org_id,
        )

    # ── Flush ──────────────────────────────────────────────────────────────────

    async def flush(self) -> int:
        if not self._buffer:
            return 0
        batch = list(self._buffer)
        self._buffer.clear()
        if self._db_writer:
            try:
                await self._db_writer("batch_usage_events", batch)
            except Exception as exc:
                log.error("UsageMeter: flush failed: %s", exc)
                self._buffer.extend(batch)   # re-queue on failure
                return 0
        return len(batch)

    # ── Query helpers ──────────────────────────────────────────────────────────

    def running_total(self, tenant_id: str, event_type: UsageEventType) -> float:
        """In-memory running total since last restart (approximate)."""
        return self._totals.get((tenant_id, event_type.value), 0.0)

    def buffer_depth(self) -> int:
        return len(self._buffer)


# ── Singleton ──────────────────────────────────────────────────────────────────

_meter: Optional[UsageMeter] = None


def get_usage_meter(db_writer: Optional[Callable] = None) -> UsageMeter:
    global _meter
    if _meter is None:
        _meter = UsageMeter(db_writer=db_writer)
    return _meter
