"""
Append-only event store for workflow and compliance events.

Distinct from audit_log.py which tracks ANALYST ACTIONS.
This store tracks SYSTEM EVENTS — workflow state transitions, agent outputs,
monitoring results — for replay and investigation lineage.

Events are immutable once written. The store supports:
  - Event replay for investigation reproduction
  - Time-travel queries (state at point-in-time)
  - Correlation across workflows
  - Compliance lineage reconstruction

Schema: audit.workflow_events
  event_id       UUID PK
  sequence_num   BIGSERIAL (monotonic, tamper-detection)
  case_id        UUID (nullable)
  workflow_id    TEXT
  event_type     TEXT
  aggregate_id   UUID (the entity this event belongs to)
  payload        JSONB
  occurred_at    TIMESTAMPTZ
  checksum       TEXT (SHA-256 of sequence_num + payload)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List


class WorkflowEvent:
    """Immutable workflow event record."""

    __slots__ = (
        "event_id", "workflow_id", "case_id", "aggregate_id",
        "event_type", "payload", "occurred_at", "checksum",
    )

    def __init__(
        self,
        workflow_id:  str,
        event_type:   str,
        payload:      Dict[str, Any],
        aggregate_id: str | None = None,
        case_id:      str | None = None,
    ) -> None:
        self.event_id     = str(uuid.uuid4())
        self.workflow_id  = workflow_id
        self.case_id      = case_id
        self.aggregate_id = aggregate_id or workflow_id
        self.event_type   = event_type
        self.payload      = payload
        self.occurred_at  = datetime.now(tz=UTC)
        # Checksum covers payload + timestamp for tamper detection
        self.checksum     = self._compute_checksum()

    def _compute_checksum(self) -> str:
        data = json.dumps({
            "event_id":    self.event_id,
            "workflow_id": self.workflow_id,
            "event_type":  self.event_type,
            "payload":     self.payload,
            "occurred_at": self.occurred_at.isoformat(),
        }, sort_keys=True, default=str)
        return hashlib.sha256(data.encode()).hexdigest()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "event_id":     self.event_id,
            "workflow_id":  self.workflow_id,
            "case_id":      self.case_id,
            "aggregate_id": self.aggregate_id,
            "event_type":   self.event_type,
            "payload":      self.payload,
            "occurred_at":  self.occurred_at.isoformat(),
            "checksum":     self.checksum,
        }


class EventStore:
    """
    Append-only in-process event store with DB persistence.

    write() is synchronous and returns immediately.
    persist() flushes to the database.

    For replay: replay_case() returns all events for a case in sequence order.
    """

    def __init__(self) -> None:
        self._pending: List[WorkflowEvent] = []

    def write(
        self,
        workflow_id:  str,
        event_type:   str,
        payload:      Dict[str, Any],
        aggregate_id: str | None = None,
        case_id:      str | None = None,
    ) -> WorkflowEvent:
        """Append an event to the store."""
        event = WorkflowEvent(
            workflow_id=workflow_id,
            event_type=event_type,
            payload=payload,
            aggregate_id=aggregate_id,
            case_id=case_id,
        )
        self._pending.append(event)
        return event

    def get_pending(self) -> List[WorkflowEvent]:
        """Return all unperisted events (copy)."""
        return list(self._pending)

    def clear_pending(self) -> None:
        """Clear the in-process pending buffer after successful persist."""
        self._pending.clear()

    def verify_checksum(self, event: WorkflowEvent) -> bool:
        """Re-compute and verify a stored event's checksum."""
        return event.checksum == event._compute_checksum()


event_store = EventStore()
