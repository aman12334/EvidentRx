"""
Human-in-the-loop review checkpoints.

Checkpoints pause workflow execution at defined points and require a human
analyst to review and continue. Unlike approval gates (binary approve/reject),
checkpoints allow the reviewer to provide structured input that influences
subsequent workflow steps.

Checkpoint triggers:
  - Confidence below threshold (configurable, default 50%)
  - Critical finding detected with insufficient evidence
  - Escalation recommendation from AI (always requires human review)
  - Anomalous entity behavior detected
  - First investigation of a new entity type

When a checkpoint is hit:
  1. Workflow state is serialized and saved (checkpoint_id)
  2. Analyst is notified (webhook / in-app)
  3. Workflow pauses (state: "awaiting_review")
  4. Analyst reviews, optionally adds context, resumes
  5. Workflow continues from saved state
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Dict, Optional


class CheckpointStatus(str, Enum):
    PENDING   = "pending"
    REVIEWED  = "reviewed"
    SKIPPED   = "skipped"     # analyst chose to skip review
    TIMED_OUT = "timed_out"   # review window expired


@dataclass
class HumanCheckpoint:
    """
    A single human review checkpoint in a workflow.
    """
    checkpoint_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id:   str = ""
    case_id:       Optional[str] = None
    tenant_id:     str = ""
    trigger:       str = ""              # why this checkpoint was hit
    node_name:     str = ""              # which workflow node triggered it
    workflow_state: Dict[str, Any] = field(default_factory=dict)  # serialized state
    status:        CheckpointStatus = CheckpointStatus.PENDING
    reviewer_id:   Optional[str] = None
    review_notes:  Optional[str] = None
    reviewer_input: Dict[str, Any] = field(default_factory=dict)  # analyst context
    created_at:    datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    reviewed_at:   Optional[datetime] = None

    def review(
        self,
        reviewer_id:    str,
        notes:          Optional[str] = None,
        reviewer_input: Optional[dict] = None,
    ) -> None:
        """Mark checkpoint as reviewed and capture analyst input."""
        self.status         = CheckpointStatus.REVIEWED
        self.reviewer_id    = reviewer_id
        self.review_notes   = notes
        self.reviewer_input = reviewer_input or {}
        self.reviewed_at    = datetime.now(tz=timezone.utc)

    def skip(self, actor_id: str) -> None:
        """Analyst acknowledges but does not provide additional input."""
        self.status      = CheckpointStatus.SKIPPED
        self.reviewer_id = actor_id
        self.reviewed_at = datetime.now(tz=timezone.utc)

    @property
    def is_resolved(self) -> bool:
        return self.status != CheckpointStatus.PENDING

    @property
    def merged_state(self) -> Dict[str, Any]:
        """Workflow state merged with analyst-provided input for resume."""
        return {**self.workflow_state, **self.reviewer_input}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id":  self.checkpoint_id,
            "workflow_id":    self.workflow_id,
            "case_id":        self.case_id,
            "tenant_id":      self.tenant_id,
            "trigger":        self.trigger,
            "node_name":      self.node_name,
            "status":         self.status.value,
            "reviewer_id":    self.reviewer_id,
            "review_notes":   self.review_notes,
            "reviewer_input": self.reviewer_input,
            "created_at":     self.created_at.isoformat(),
            "reviewed_at":    self.reviewed_at.isoformat() if self.reviewed_at else None,
        }
