"""
Feedback event bus integration.

Publishes feedback events to the interoperability event bus so downstream
systems (calibration engine, analytics, governance) can react without
tight coupling to the feedback collector.

Event topics
────────────
  evidentrx.{tenant_id}.feedback.{feedback_type}
    — raw feedback events as they arrive
  evidentrx.{tenant_id}.feedback.accepted
    — feedback that has been accepted for calibration
  evidentrx.{tenant_id}.feedback.lineage
    — lineage chain events

All events carry only non-PHI fields. analyst_id is included (it's a
platform user ID, not patient data).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing   import Any, Optional

from learning.feedback.models   import FeedbackRecord, FeedbackType
from learning.feedback.lineage  import LineageEntry

log = logging.getLogger("evidentrx.learning.feedback.events")


class FeedbackEventEmitter:
    """
    Emits feedback events to the event bus.

    Uses the interoperability event bus abstraction so that the feedback
    layer does not directly depend on Kafka or any specific broker.
    """

    def __init__(self, event_bus: Optional[Any] = None) -> None:
        self._bus = event_bus

    async def emit_feedback(self, feedback: FeedbackRecord) -> None:
        """Publish a feedback event to the type-specific topic."""
        if self._bus is None:
            log.debug("FeedbackEventEmitter: no bus configured — skipping emit")
            return

        try:
            from interoperability.streaming.event_bus import BusMessage, canonical_topic

            topic = f"evidentrx.{feedback.tenant_id}.feedback.{feedback.feedback_type.value}"
            msg   = BusMessage(
                topic         = topic,
                payload       = _feedback_to_event(feedback),
                partition_key = f"{feedback.tenant_id}:{feedback.feedback_type.value}",
            )
            await self._bus.publish(msg)
        except Exception as exc:
            log.error("FeedbackEventEmitter: emit failed: %s", exc)

    async def emit_accepted(self, feedback: FeedbackRecord) -> None:
        """Publish to the accepted-feedback topic for downstream calibration."""
        if self._bus is None:
            return
        try:
            from interoperability.streaming.event_bus import BusMessage
            msg = BusMessage(
                topic         = f"evidentrx.{feedback.tenant_id}.feedback.accepted",
                payload       = _feedback_to_event(feedback),
                partition_key = feedback.tenant_id,
            )
            await self._bus.publish(msg)
        except Exception as exc:
            log.error("FeedbackEventEmitter: emit_accepted failed: %s", exc)

    async def emit_lineage(self, entry: LineageEntry) -> None:
        """Publish a lineage chain event."""
        if self._bus is None:
            return
        try:
            from interoperability.streaming.event_bus import BusMessage
            msg = BusMessage(
                topic         = f"evidentrx.{entry.tenant_id}.feedback.lineage",
                payload       = entry.to_dict(),
                partition_key = entry.tenant_id,
            )
            await self._bus.publish(msg)
        except Exception as exc:
            log.error("FeedbackEventEmitter: emit_lineage failed: %s", exc)


def _feedback_to_event(feedback: FeedbackRecord) -> dict[str, Any]:
    """Convert a feedback record to a safe event payload (no PHI)."""
    return {
        "feedback_id":   feedback.feedback_id,
        "feedback_type": feedback.feedback_type.value,
        "tenant_id":     feedback.tenant_id,
        "analyst_id":    feedback.analyst_id,    # platform user ID, not PHI
        "artifact_type": feedback.artifact_type,
        "artifact_id":   feedback.artifact_id,
        "status":        feedback.status.value,
        "created_at":    feedback.created_at.isoformat(),
        "lineage_hash":  feedback.lineage_hash,
    }
