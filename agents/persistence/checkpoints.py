"""
CheckpointManager — persists and retrieves LangGraph workflow state
to/from audit.workflow_checkpoints.

Every node calls save() after completing. This provides:
  - Resume capability if the workflow is interrupted
  - Full audit trail of workflow progression
  - State inspection for debugging and compliance review

The checkpoint is NOT the LangGraph internal checkpoint (MemorySaver) —
it is our DB-level audit checkpoint that is always written regardless of
LangGraph's internal state management.
"""
from __future__ import annotations

import json
import logging
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class CheckpointManager:
    def save(
        self,
        session: Session,
        *,
        case_id: UUID,
        run_id: str,
        agent_run_id: Optional[UUID],
        workflow_name: str,
        checkpoint_name: str,
        state: dict,
        is_resumable: bool = True,
    ) -> UUID:
        """
        Save a workflow checkpoint. Marks prior checkpoints for this
        (case_id, workflow_name) as non-resumable before inserting.
        Returns the new checkpoint_id.
        """
        # Mark previous checkpoints as non-resumable
        session.execute(text("""
            UPDATE audit.workflow_checkpoints
            SET is_resumable = FALSE
            WHERE case_id = :case_id
              AND workflow_name = :workflow_name
              AND is_resumable = TRUE
        """), {"case_id": str(case_id), "workflow_name": workflow_name})

        checkpoint_id = uuid4()

        # Sanitize state for JSON serialization (remove non-serializable objects)
        serializable_state = _sanitize_state(state)

        session.execute(text("""
            INSERT INTO audit.workflow_checkpoints (
                checkpoint_id, case_id, agent_run_id,
                workflow_name, checkpoint_name,
                state_data, is_resumable
            ) VALUES (
                :checkpoint_id, :case_id, :agent_run_id,
                :workflow_name, :checkpoint_name,
                CAST(:state_data AS jsonb), :is_resumable
            )
        """), {
            "checkpoint_id":   str(checkpoint_id),
            "case_id":         str(case_id),
            "agent_run_id":    str(agent_run_id) if agent_run_id else None,
            "workflow_name":   workflow_name,
            "checkpoint_name": checkpoint_name,
            "state_data":      json.dumps(serializable_state),
            "is_resumable":    is_resumable,
        })

        logger.debug(
            "Checkpoint saved: %s node=%s case=%s",
            checkpoint_id, checkpoint_name, case_id,
        )
        return checkpoint_id

    def load_latest(
        self,
        session: Session,
        case_id: UUID,
        workflow_name: str,
    ) -> Optional[dict]:
        """
        Returns the most recent resumable checkpoint state for a case,
        or None if no resumable checkpoint exists.
        """
        row = session.execute(text("""
            SELECT checkpoint_id, checkpoint_name, state_data, created_at
            FROM audit.workflow_checkpoints
            WHERE case_id = :case_id
              AND workflow_name = :workflow_name
              AND is_resumable = TRUE
            ORDER BY created_at DESC
            LIMIT 1
        """), {"case_id": str(case_id), "workflow_name": workflow_name}).fetchone()

        if not row:
            return None

        return {
            "checkpoint_id":   str(row.checkpoint_id),
            "checkpoint_name": row.checkpoint_name,
            "state":           row.state_data,
            "created_at":      row.created_at.isoformat(),
        }

    def list_checkpoints(
        self,
        session: Session,
        case_id: UUID,
    ) -> list[dict]:
        """Returns all checkpoints for a case ordered chronologically."""
        rows = session.execute(text("""
            SELECT checkpoint_id, workflow_name, checkpoint_name,
                   is_resumable, created_at
            FROM audit.workflow_checkpoints
            WHERE case_id = :case_id
            ORDER BY created_at ASC
        """), {"case_id": str(case_id)}).fetchall()

        return [
            {
                "checkpoint_id":   str(r.checkpoint_id),
                "workflow_name":   r.workflow_name,
                "checkpoint_name": r.checkpoint_name,
                "is_resumable":    r.is_resumable,
                "created_at":      r.created_at.isoformat(),
            }
            for r in rows
        ]


def _sanitize_state(state: dict) -> dict:
    """
    Recursively serialize a state dict to JSON-safe types.
    Removes None UUIDs and converts date/UUID objects.
    """
    import uuid
    import datetime

    def _convert(v):
        if isinstance(v, uuid.UUID):
            return str(v)
        if isinstance(v, (datetime.date, datetime.datetime)):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: _convert(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_convert(i) for i in v]
        return v

    return {k: _convert(v) for k, v in state.items()}
