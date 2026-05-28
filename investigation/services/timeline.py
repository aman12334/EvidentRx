"""
TimelineService — append-only event recording for investigation cases.

Every state change, finding addition, agent trigger, and human action
is recorded here as an immutable event. The timeline is the audit trail.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# All valid event types — exhaustive enum kept here, not in DB
EVENT_TYPES = frozenset({
    "CASE_CREATED",
    "FINDING_ADDED",
    "FINDING_REMOVED",
    "STATUS_CHANGED",
    "PRIORITY_CHANGED",
    "ASSIGNMENT_CHANGED",
    "ESCALATED",
    "SNAPSHOT_TAKEN",
    "AGENT_TRIGGERED",
    "AGENT_COMPLETED",
    "AGENT_FAILED",
    "CHECKPOINT_SAVED",
    "HUMAN_ACTION",
    "NOTE_ADDED",
    "FINANCIAL_EXPOSURE_UPDATED",
})


class TimelineService:
    def record(
        self,
        session: Session,
        case_id: UUID,
        event_type: str,
        event_data: dict,
        actor_id: str | None = None,
        actor_type: str = "system",
    ) -> UUID:
        """
        Append an immutable event to the case timeline.
        Returns the new event_id.
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event_type '{event_type}'. Must be one of: {sorted(EVENT_TYPES)}")

        event_id = uuid4()
        now = datetime.now(UTC)

        session.execute(text("""
            INSERT INTO audit.investigation_timelines
                (event_id, case_id, event_type, event_data, actor_id, actor_type, occurred_at)
            VALUES
                (:event_id, :case_id, :event_type, CAST(:event_data AS jsonb), :actor_id, :actor_type, :occurred_at)
        """), {
            "event_id": str(event_id),
            "case_id": str(case_id),
            "event_type": event_type,
            "event_data": json.dumps(event_data),
            "actor_id": actor_id,
            "actor_type": actor_type,
            "occurred_at": now,
        })

        return event_id

    def get_timeline(
        self,
        session: Session,
        case_id: UUID,
        limit: int = 200,
    ) -> list[dict]:
        """
        Returns the ordered timeline for a case, oldest first.
        """
        rows = session.execute(text("""
            SELECT event_id, event_type, event_data, actor_id, actor_type,
                   occurred_at, sequence_number
            FROM audit.investigation_timelines
            WHERE case_id = :case_id
            ORDER BY occurred_at ASC, sequence_number ASC
            LIMIT :limit
        """), {"case_id": str(case_id), "limit": limit}).fetchall()

        return [
            {
                "event_id": str(r.event_id),
                "event_type": r.event_type,
                "event_data": r.event_data,
                "actor_id": r.actor_id,
                "actor_type": r.actor_type,
                "occurred_at": r.occurred_at.isoformat(),
            }
            for r in rows
        ]

    def get_status_history(self, session: Session, case_id: UUID) -> list[dict]:
        """Returns only STATUS_CHANGED events — the lifecycle audit trail."""
        rows = session.execute(text("""
            SELECT event_data, actor_id, actor_type, occurred_at
            FROM audit.investigation_timelines
            WHERE case_id = :case_id
              AND event_type = 'STATUS_CHANGED'
            ORDER BY occurred_at ASC, sequence_number ASC
        """), {"case_id": str(case_id)}).fetchall()

        return [
            {
                "from": r.event_data.get("from"),
                "to": r.event_data.get("to"),
                "actor_id": r.actor_id,
                "actor_type": r.actor_type,
                "occurred_at": r.occurred_at.isoformat(),
                "notes": r.event_data.get("notes"),
            }
            for r in rows
        ]
