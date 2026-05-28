"""
Workflow approval gates.

Certain high-risk workflow transitions require explicit approval before
proceeding. This prevents autonomous AI actions from crossing critical
compliance thresholds without human awareness.

Gate types:
  ESCALATION:   Case escalation requires senior analyst review
  RESOLUTION:   Case resolution with low confidence requires auditor sign-off
  MODEL_CHANGE: Switching the active model mid-workflow requires admin approval
  BULK_ACTION:  Bulk status changes require senior analyst approval

Gates are non-blocking by default (async approval) but can be configured as
blocking (workflow pauses until approved or rejected).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Dict, List, Optional


class ApprovalStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED  = "expired"
    BYPASSED = "bypassed"   # system/admin bypass with audit trail


class GateType(str, Enum):
    ESCALATION   = "escalation"
    RESOLUTION   = "resolution"
    MODEL_CHANGE = "model_change"
    BULK_ACTION  = "bulk_action"
    ARCHIVE      = "archive"


@dataclass
class ApprovalGate:
    """A single approval gate instance for a workflow decision."""
    gate_id:       str = field(default_factory=lambda: str(uuid.uuid4()))
    gate_type:     GateType = GateType.ESCALATION
    case_id:       Optional[str] = None
    workflow_id:   Optional[str] = None
    tenant_id:     str = ""
    requested_by:  str = ""
    context:       Dict = field(default_factory=dict)
    status:        ApprovalStatus = ApprovalStatus.PENDING
    reviewed_by:   Optional[str] = None
    reviewed_at:   Optional[datetime] = None
    notes:         Optional[str] = None
    created_at:    datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    expires_at:    Optional[datetime] = None

    def approve(self, reviewer_id: str, notes: Optional[str] = None) -> None:
        self.status      = ApprovalStatus.APPROVED
        self.reviewed_by = reviewer_id
        self.reviewed_at = datetime.now(tz=timezone.utc)
        self.notes       = notes

    def reject(self, reviewer_id: str, notes: Optional[str] = None) -> None:
        self.status      = ApprovalStatus.REJECTED
        self.reviewed_by = reviewer_id
        self.reviewed_at = datetime.now(tz=timezone.utc)
        self.notes       = notes

    def bypass(self, actor_id: str, reason: str) -> None:
        """Admin bypass — recorded but still audited."""
        self.status      = ApprovalStatus.BYPASSED
        self.reviewed_by = actor_id
        self.reviewed_at = datetime.now(tz=timezone.utc)
        self.notes       = f"BYPASSED: {reason}"

    @property
    def is_resolved(self) -> bool:
        return self.status != ApprovalStatus.PENDING

    @property
    def is_approved(self) -> bool:
        return self.status in (ApprovalStatus.APPROVED, ApprovalStatus.BYPASSED)

    def as_dict(self) -> dict:
        return {
            "gate_id":     self.gate_id,
            "gate_type":   self.gate_type.value,
            "case_id":     self.case_id,
            "tenant_id":   self.tenant_id,
            "status":      self.status.value,
            "requested_by": self.requested_by,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "notes":       self.notes,
            "created_at":  self.created_at.isoformat(),
        }


class ApprovalGateRegistry:
    """In-process approval gate store. Production: persist to DB."""

    def __init__(self) -> None:
        self._gates: Dict[str, ApprovalGate] = {}

    def create(
        self,
        gate_type:   GateType,
        tenant_id:   str,
        requested_by: str,
        case_id:     Optional[str] = None,
        context:     Optional[dict] = None,
    ) -> ApprovalGate:
        gate = ApprovalGate(
            gate_type=gate_type,
            tenant_id=tenant_id,
            requested_by=requested_by,
            case_id=case_id,
            context=context or {},
        )
        self._gates[gate.gate_id] = gate
        return gate

    def get(self, gate_id: str) -> Optional[ApprovalGate]:
        return self._gates.get(gate_id)

    def list_pending(self, tenant_id: str) -> List[ApprovalGate]:
        return [
            g for g in self._gates.values()
            if g.status == ApprovalStatus.PENDING and g.tenant_id == tenant_id
        ]


approval_registry = ApprovalGateRegistry()
