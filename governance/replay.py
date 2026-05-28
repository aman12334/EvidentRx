"""
Governance replay infrastructure.

Enables reproducible replay of any investigation workflow for:
  - Compliance audit reproduction
  - Dispute resolution
  - Model calibration verification
  - Regulator examination support

Replay is READ-ONLY — it reconstructs the investigation state at a point-in-
time but does not modify any DB records. All replay actions are audited.

Replay modes:
  FULL:       Replay all events from start to a given timestamp
  CHECKPOINT: Resume from a specific workflow checkpoint
  DIFF:       Compare two replay runs (for model drift detection)
"""

from __future__ import annotations

import logging
from datetime   import datetime, timezone
from enum       import Enum
from typing     import Any, Dict, List, Optional

from governance.audit_log   import audit_log, AuditEventType
from governance.event_store import WorkflowEvent

log = logging.getLogger(__name__)


class ReplayMode(str, Enum):
    FULL       = "full"
    CHECKPOINT = "checkpoint"
    DIFF       = "diff"


class ReplayResult:
    """Result of a governance replay run."""

    def __init__(
        self,
        replay_id:    str,
        case_id:      str,
        mode:         ReplayMode,
        events_count: int,
        final_state:  Dict[str, Any],
        started_at:   datetime,
        completed_at: datetime,
    ) -> None:
        self.replay_id    = replay_id
        self.case_id      = case_id
        self.mode         = mode
        self.events_count = events_count
        self.final_state  = final_state
        self.started_at   = started_at
        self.completed_at = completed_at
        self.duration_ms  = int(
            (completed_at - started_at).total_seconds() * 1000
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "replay_id":    self.replay_id,
            "case_id":      self.case_id,
            "mode":         self.mode.value,
            "events_count": self.events_count,
            "final_state":  self.final_state,
            "started_at":   self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_ms":  self.duration_ms,
        }


class GovernanceReplayer:
    """
    Replays investigation workflows from persisted event streams.
    """

    def replay_case(
        self,
        case_id:       str,
        actor_id:      str,
        tenant_id:     str,
        events:        List[WorkflowEvent],
        mode:          ReplayMode = ReplayMode.FULL,
        up_to:         Optional[datetime] = None,
        checkpoint_id: Optional[str] = None,
    ) -> ReplayResult:
        """
        Replay events for a case and reconstruct its state.

        Args:
            events:        Ordered list of WorkflowEvent from the event store
            up_to:         Replay only events up to this timestamp (time-travel)
            checkpoint_id: Resume from this checkpoint event_id (CHECKPOINT mode)
        """
        import uuid
        replay_id  = str(uuid.uuid4())
        started_at = datetime.now(tz=timezone.utc)

        log.info(
            "Replay started: case=%s mode=%s actor=%s replay_id=%s",
            case_id, mode.value, actor_id, replay_id,
        )

        # Filter events by time window
        replay_events = events
        if up_to:
            replay_events = [e for e in events if e.occurred_at <= up_to]

        # Checkpoint mode: skip events before checkpoint
        if mode == ReplayMode.CHECKPOINT and checkpoint_id:
            found = False
            filtered = []
            for e in replay_events:
                if e.event_id == checkpoint_id:
                    found = True
                if found:
                    filtered.append(e)
            replay_events = filtered

        # Reconstruct state by applying events in sequence
        state = self._apply_events(replay_events)

        completed_at = datetime.now(tz=timezone.utc)

        result = ReplayResult(
            replay_id=replay_id,
            case_id=case_id,
            mode=mode,
            events_count=len(replay_events),
            final_state=state,
            started_at=started_at,
            completed_at=completed_at,
        )

        # Audit the replay itself
        audit_log.write(
            event_type=AuditEventType.WORKFLOW_REPLAYED,
            actor_id=actor_id,
            tenant_id=tenant_id,
            payload={
                "replay_id":    replay_id,
                "mode":         mode.value,
                "events_count": len(replay_events),
                "up_to":        up_to.isoformat() if up_to else None,
                "duration_ms":  result.duration_ms,
            },
            resource_id=case_id,
            resource_type="investigation_case",
        )

        log.info(
            "Replay completed: case=%s events=%d duration=%dms",
            case_id, len(replay_events), result.duration_ms,
        )

        return result

    def _apply_events(self, events: List[WorkflowEvent]) -> Dict[str, Any]:
        """
        Fold event stream into final state dict.
        Each event's payload is merged into the accumulated state.
        """
        state: Dict[str, Any] = {
            "events_applied": 0,
            "timeline":       [],
        }

        for event in events:
            state["events_applied"] += 1
            state["timeline"].append({
                "event_id":   event.event_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at.isoformat(),
            })
            # Merge event payload fields that represent state changes
            for k, v in event.payload.items():
                if k not in ("event_id", "occurred_at"):
                    state[k] = v

        return state


governance_replayer = GovernanceReplayer()
