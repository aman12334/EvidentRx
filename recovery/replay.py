"""
Investigation replay for disaster recovery and audit reproduction.

Enables full reconstruction of any investigation's execution history from the
event store. Used for:
  - Regulator examination (reproduce exactly what happened)
  - Model drift detection (replay with new model, compare outputs)
  - Post-incident review (understand what the system did)
  - Determinism verification (same inputs → same finding outputs)

Replay process:
  1. Load all WorkflowEvents for the case from DB (ordered by sequence_num)
  2. Apply events to reconstruct state at any point in time
  3. Optionally re-execute workflow nodes with saved inputs (for model comparison)
  4. Write ReplayResult to audit log (all replays are traced)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from governance.audit_log import AuditEventType, audit_log
from governance.event_store import WorkflowEvent

log = logging.getLogger("evidentrx.recovery.replay")


class InvestigationReplayer:
    """
    Reconstructs investigation state from the event store.
    All replays are READ-ONLY — no DB writes occur during replay.
    """

    def replay(
        self,
        case_id:   str,
        actor_id:  str,
        tenant_id: str,
        events:    List[WorkflowEvent],
        up_to:     datetime | None = None,
    ) -> Dict[str, Any]:
        """
        Replay events and return the reconstructed case state.

        Args:
            events: Ordered list of WorkflowEvent records (from event store)
            up_to:  If set, only replay events up to this timestamp
        """
        import uuid
        replay_id = str(uuid.uuid4())
        log.info(
            "Recovery replay started: case=%s actor=%s replay_id=%s",
            case_id, actor_id, replay_id,
        )

        # Filter by time window
        filtered = events
        if up_to:
            filtered = [e for e in events if e.occurred_at <= up_to]

        # Reconstruct state
        state: Dict[str, Any] = {
            "case_id":       case_id,
            "replay_id":     replay_id,
            "events_applied": 0,
            "timeline":      [],
        }

        for event in filtered:
            state["events_applied"] += 1
            state["timeline"].append({
                "event_id":    event.event_id,
                "event_type":  event.event_type,
                "occurred_at": event.occurred_at.isoformat(),
                "checksum":    event.checksum,
            })
            # Apply state mutations from event payload
            for k, v in event.payload.items():
                if k not in ("event_id", "occurred_at", "checksum"):
                    state[k] = v

        # Audit the replay
        audit_log.write(
            event_type=AuditEventType.WORKFLOW_REPLAYED,
            actor_id=actor_id,
            tenant_id=tenant_id,
            payload={
                "replay_id":     replay_id,
                "case_id":       case_id,
                "events_applied": state["events_applied"],
                "up_to":         up_to.isoformat() if up_to else None,
            },
            resource_id=case_id,
            resource_type="investigation_case",
        )

        log.info(
            "Recovery replay completed: case=%s events=%d",
            case_id, state["events_applied"],
        )
        return state

    def list_checkpoints(self, events: List[WorkflowEvent]) -> List[Dict[str, Any]]:
        """
        Extract checkpoint events from the event stream.
        Returns a list of checkpoint markers for time-travel selection.
        """
        return [
            {
                "event_id":    e.event_id,
                "event_type":  e.event_type,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in events
            if "checkpoint" in e.event_type.lower() or "node_completed" in e.event_type.lower()
        ]


investigation_replayer = InvestigationReplayer()
