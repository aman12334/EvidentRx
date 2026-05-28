"""
Regulatory governance workflow engine.

Manages the approval-gated lifecycle of policy changes within the platform.
A PolicyActivationWorkflow governs the process by which a new or updated
regulatory document is reviewed, approved, and activated for a tenant —
ensuring no regulatory document affects compliance operations without
explicit human sign-off.

Workflow stages
───────────────
  PENDING_REVIEW  → document has been ingested; awaiting initial review
  UNDER_REVIEW    → a compliance officer has claimed the review
  CHANGES_REQUESTED → reviewer has flagged issues requiring resolution
  APPROVED        → designated compliance officer has approved activation
  ACTIVATED       → document is now active in the tenant's policy set
  REJECTED        → document rejected; will not be activated
  SUPERSEDED      → this activation was replaced by a newer workflow
  WITHDRAWN       → withdrawn before completion (e.g. document recalled)

Invariants
──────────
1. Only PENDING_REVIEW workflows can be claimed (start_review)
2. Only UNDER_REVIEW workflows can be approved, rejected, or flagged
3. Only APPROVED workflows can be activated
4. Activation must be performed by a different actor than the approver
5. Approval requires a different actor than the reviewer
6. Self-approval is always blocked
7. All state transitions are immutably audit-trailed
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.regulatory.governance.workflows")


class WorkflowStatus(str, Enum):
    PENDING_REVIEW     = "pending_review"
    UNDER_REVIEW       = "under_review"
    CHANGES_REQUESTED  = "changes_requested"
    APPROVED           = "approved"
    ACTIVATED          = "activated"
    REJECTED           = "rejected"
    SUPERSEDED         = "superseded"
    WITHDRAWN          = "withdrawn"


class WorkflowPriority(str, Enum):
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"
    URGENT = "urgent"


@dataclass
class WorkflowAuditEntry:
    """One immutable audit record for a workflow state transition."""
    entry_id:    str
    workflow_id: str
    from_status: Optional[WorkflowStatus]
    to_status:   WorkflowStatus
    actor_id:    str
    action:      str       # human-readable action label
    notes:       str       = ""
    occurred_at: datetime  = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":    self.entry_id,
            "workflow_id": self.workflow_id,
            "from_status": self.from_status.value if self.from_status else None,
            "to_status":   self.to_status.value,
            "actor_id":    self.actor_id,
            "action":      self.action,
            "notes":       self.notes,
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass
class PolicyActivationWorkflow:
    """
    Governs the review-approve-activate lifecycle for a regulatory document.

    Tracks who reviewed, who approved, who activated, and why — providing
    a complete, auditable chain of human decisions for every activated doc.
    """
    workflow_id:     str
    tenant_id:       str
    doc_id:          str
    doc_version:     str
    doc_title:       str
    status:          WorkflowStatus
    priority:        WorkflowPriority
    created_by:      str
    created_at:      datetime          = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    reviewer_id:     Optional[str]     = None
    review_started_at: Optional[datetime] = None
    approver_id:     Optional[str]     = None
    approved_at:     Optional[datetime]   = None
    approval_notes:  str               = ""
    activator_id:    Optional[str]     = None
    activated_at:    Optional[datetime]   = None
    rejected_by:     Optional[str]     = None
    rejected_at:     Optional[datetime]   = None
    rejection_reason: str              = ""
    action_required_by: Optional[str] = None   # ISO-8601 date deadline
    audit_trail:     list[WorkflowAuditEntry] = field(default_factory=list)
    metadata:        dict[str, Any]    = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            WorkflowStatus.ACTIVATED,
            WorkflowStatus.REJECTED,
            WorkflowStatus.SUPERSEDED,
            WorkflowStatus.WITHDRAWN,
        }

    @property
    def awaiting_action(self) -> bool:
        return self.status in {
            WorkflowStatus.PENDING_REVIEW,
            WorkflowStatus.UNDER_REVIEW,
            WorkflowStatus.CHANGES_REQUESTED,
            WorkflowStatus.APPROVED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id":       self.workflow_id,
            "tenant_id":         self.tenant_id,
            "doc_id":            self.doc_id,
            "doc_version":       self.doc_version,
            "doc_title":         self.doc_title,
            "status":            self.status.value,
            "priority":          self.priority.value,
            "created_by":        self.created_by,
            "created_at":        self.created_at.isoformat(),
            "reviewer_id":       self.reviewer_id,
            "review_started_at": self.review_started_at.isoformat() if self.review_started_at else None,
            "approver_id":       self.approver_id,
            "approved_at":       self.approved_at.isoformat() if self.approved_at else None,
            "approval_notes":    self.approval_notes,
            "activator_id":      self.activator_id,
            "activated_at":      self.activated_at.isoformat() if self.activated_at else None,
            "rejected_by":       self.rejected_by,
            "rejected_at":       self.rejected_at.isoformat() if self.rejected_at else None,
            "rejection_reason":  self.rejection_reason,
            "action_required_by":self.action_required_by,
            "audit_trail_length":len(self.audit_trail),
        }


class RegulatoryReviewWorkflow:
    """
    Manages the full review-approve-activate lifecycle for regulatory documents.

    All state transitions enforce governance invariants:
    - Sequential status progression (no skipping stages)
    - Actor separation (reviewer ≠ approver, approver ≠ activator)
    - Self-approval prevention
    - Immutable audit trail for every transition
    """

    def __init__(self) -> None:
        # workflow_id → PolicyActivationWorkflow
        self._workflows: dict[str, PolicyActivationWorkflow] = {}

    # ── Creation ────────────────────────────────────────────────────────────────

    def create(
        self,
        tenant_id:           str,
        doc_id:              str,
        doc_version:         str,
        doc_title:           str,
        created_by:          str,
        priority:            WorkflowPriority = WorkflowPriority.NORMAL,
        action_required_by:  Optional[str]   = None,
        metadata:            Optional[dict]  = None,
    ) -> PolicyActivationWorkflow:
        """Create a new governance workflow for a document awaiting review."""
        wf = PolicyActivationWorkflow(
            workflow_id        = str(uuid.uuid4()),
            tenant_id          = tenant_id,
            doc_id             = doc_id,
            doc_version        = doc_version,
            doc_title          = doc_title,
            status             = WorkflowStatus.PENDING_REVIEW,
            priority           = priority,
            created_by         = created_by,
            action_required_by = action_required_by,
            metadata           = metadata or {},
        )
        wf.audit_trail.append(self._audit(wf.workflow_id, None, WorkflowStatus.PENDING_REVIEW, created_by, "created"))
        self._workflows[wf.workflow_id] = wf
        log.info(
            "RegulatoryReviewWorkflow: created workflow %s for doc %s v%s (tenant %s)",
            wf.workflow_id[:8], doc_id[:8], doc_version, tenant_id[:8],
        )
        return wf

    # ── State transitions ────────────────────────────────────────────────────────

    def start_review(
        self,
        tenant_id:   str,
        workflow_id: str,
        reviewer_id: str,
    ) -> PolicyActivationWorkflow:
        """Claim a workflow for review. Only PENDING_REVIEW workflows can be claimed."""
        wf = self._get_owned(tenant_id, workflow_id)
        if wf.status != WorkflowStatus.PENDING_REVIEW:
            raise WorkflowError(
                f"Workflow {workflow_id[:8]} is {wf.status.value}; "
                f"only PENDING_REVIEW workflows can be claimed"
            )
        prev                  = wf.status
        wf.status             = WorkflowStatus.UNDER_REVIEW
        wf.reviewer_id        = reviewer_id
        wf.review_started_at  = datetime.now(tz=timezone.utc)
        wf.audit_trail.append(self._audit(workflow_id, prev, wf.status, reviewer_id, "review_started"))
        return wf

    def request_changes(
        self,
        tenant_id:   str,
        workflow_id: str,
        reviewer_id: str,
        notes:       str,
    ) -> PolicyActivationWorkflow:
        """Flag issues requiring resolution before approval."""
        wf = self._get_owned(tenant_id, workflow_id)
        if wf.status != WorkflowStatus.UNDER_REVIEW:
            raise WorkflowError(f"Workflow {workflow_id[:8]} is not UNDER_REVIEW")
        if wf.reviewer_id and wf.reviewer_id != reviewer_id:
            raise WorkflowError("Only the assigned reviewer can request changes")
        prev       = wf.status
        wf.status  = WorkflowStatus.CHANGES_REQUESTED
        wf.audit_trail.append(
            self._audit(workflow_id, prev, wf.status, reviewer_id, "changes_requested", notes)
        )
        return wf

    def resubmit(
        self,
        tenant_id:   str,
        workflow_id: str,
        actor_id:    str,
        notes:       str = "",
    ) -> PolicyActivationWorkflow:
        """Return a CHANGES_REQUESTED workflow to UNDER_REVIEW after resolution."""
        wf = self._get_owned(tenant_id, workflow_id)
        if wf.status != WorkflowStatus.CHANGES_REQUESTED:
            raise WorkflowError(f"Workflow {workflow_id[:8]} is not in CHANGES_REQUESTED state")
        prev       = wf.status
        wf.status  = WorkflowStatus.UNDER_REVIEW
        wf.audit_trail.append(
            self._audit(workflow_id, prev, wf.status, actor_id, "resubmitted", notes)
        )
        return wf

    def approve(
        self,
        tenant_id:   str,
        workflow_id: str,
        approver_id: str,
        notes:       str = "",
    ) -> PolicyActivationWorkflow:
        """
        Approve a workflow for activation.

        Invariants enforced:
        - Workflow must be UNDER_REVIEW
        - Approver must be different from the reviewer (four-eyes principle)
        """
        wf = self._get_owned(tenant_id, workflow_id)
        if wf.status != WorkflowStatus.UNDER_REVIEW:
            raise WorkflowError(f"Workflow {workflow_id[:8]} is not UNDER_REVIEW")
        if wf.reviewer_id and approver_id == wf.reviewer_id:
            raise WorkflowError(
                "Approver must be a different person from the reviewer (four-eyes principle)"
            )
        prev               = wf.status
        wf.status          = WorkflowStatus.APPROVED
        wf.approver_id     = approver_id
        wf.approved_at     = datetime.now(tz=timezone.utc)
        wf.approval_notes  = notes
        wf.audit_trail.append(
            self._audit(workflow_id, prev, wf.status, approver_id, "approved", notes)
        )
        log.info(
            "RegulatoryReviewWorkflow: workflow %s APPROVED by %s",
            workflow_id[:8], approver_id[:8],
        )
        return wf

    def reject(
        self,
        tenant_id:   str,
        workflow_id: str,
        rejected_by: str,
        reason:      str,
    ) -> PolicyActivationWorkflow:
        """Reject a workflow. The document will not be activated."""
        wf = self._get_owned(tenant_id, workflow_id)
        if wf.status not in {WorkflowStatus.UNDER_REVIEW, WorkflowStatus.CHANGES_REQUESTED}:
            raise WorkflowError(
                f"Workflow {workflow_id[:8]} cannot be rejected in {wf.status.value} state"
            )
        prev                 = wf.status
        wf.status            = WorkflowStatus.REJECTED
        wf.rejected_by       = rejected_by
        wf.rejected_at       = datetime.now(tz=timezone.utc)
        wf.rejection_reason  = reason
        wf.audit_trail.append(
            self._audit(workflow_id, prev, wf.status, rejected_by, "rejected", reason)
        )
        return wf

    def activate(
        self,
        tenant_id:    str,
        workflow_id:  str,
        activator_id: str,
    ) -> PolicyActivationWorkflow:
        """
        Activate the document after approval.

        Invariants enforced:
        - Workflow must be APPROVED
        - Activator must be different from the approver
        """
        wf = self._get_owned(tenant_id, workflow_id)
        if wf.status != WorkflowStatus.APPROVED:
            raise WorkflowError(
                f"Workflow {workflow_id[:8]} must be APPROVED before activation"
            )
        if wf.approver_id and activator_id == wf.approver_id:
            raise WorkflowError(
                "Activator must be a different person from the approver"
            )
        prev               = wf.status
        wf.status          = WorkflowStatus.ACTIVATED
        wf.activator_id    = activator_id
        wf.activated_at    = datetime.now(tz=timezone.utc)
        wf.audit_trail.append(
            self._audit(workflow_id, prev, wf.status, activator_id, "activated")
        )
        log.info(
            "RegulatoryReviewWorkflow: workflow %s ACTIVATED by %s — doc %s v%s",
            workflow_id[:8], activator_id[:8], wf.doc_id[:8], wf.doc_version,
        )
        return wf

    def withdraw(
        self,
        tenant_id:    str,
        workflow_id:  str,
        withdrawn_by: str,
        reason:       str = "",
    ) -> PolicyActivationWorkflow:
        """Withdraw a workflow that has not yet been activated or rejected."""
        wf = self._get_owned(tenant_id, workflow_id)
        if wf.is_terminal:
            raise WorkflowError(
                f"Cannot withdraw a workflow in {wf.status.value} state"
            )
        prev       = wf.status
        wf.status  = WorkflowStatus.WITHDRAWN
        wf.audit_trail.append(
            self._audit(workflow_id, prev, wf.status, withdrawn_by, "withdrawn", reason)
        )
        return wf

    def supersede(
        self,
        tenant_id:       str,
        workflow_id:     str,
        superseded_by:   str,
        new_workflow_id: str,
    ) -> PolicyActivationWorkflow:
        """Mark a workflow as superseded by a newer workflow for the same document."""
        wf = self._get_owned(tenant_id, workflow_id)
        if wf.status in {WorkflowStatus.SUPERSEDED, WorkflowStatus.REJECTED}:
            raise WorkflowError(
                f"Workflow {workflow_id[:8]} is already {wf.status.value}"
            )
        prev       = wf.status
        wf.status  = WorkflowStatus.SUPERSEDED
        wf.audit_trail.append(
            self._audit(
                workflow_id, prev, wf.status,
                superseded_by, "superseded",
                f"Superseded by workflow {new_workflow_id[:8]}",
            )
        )
        return wf

    # ── Queries ─────────────────────────────────────────────────────────────────

    def get(self, workflow_id: str) -> Optional[PolicyActivationWorkflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(
        self,
        tenant_id: str,
        status:    Optional[WorkflowStatus] = None,
        limit:     int = 50,
    ) -> list[PolicyActivationWorkflow]:
        wfs = [
            w for w in self._workflows.values()
            if w.tenant_id == tenant_id
            and (status is None or w.status == status)
        ]
        wfs.sort(key=lambda w: w.created_at, reverse=True)
        return wfs[:limit]

    def pending_action(self, tenant_id: str) -> list[PolicyActivationWorkflow]:
        """Return workflows that require action from a human actor."""
        return [
            w for w in self._workflows.values()
            if w.tenant_id == tenant_id and w.awaiting_action
        ]

    def workflows_for_document(
        self,
        tenant_id: str,
        doc_id:    str,
    ) -> list[PolicyActivationWorkflow]:
        wfs = [
            w for w in self._workflows.values()
            if w.tenant_id == tenant_id and w.doc_id == doc_id
        ]
        wfs.sort(key=lambda w: w.created_at, reverse=True)
        return wfs

    # ── Private ─────────────────────────────────────────────────────────────────

    def _get_owned(self, tenant_id: str, workflow_id: str) -> PolicyActivationWorkflow:
        wf = self._workflows.get(workflow_id)
        if wf is None or wf.tenant_id != tenant_id:
            raise WorkflowError(f"Workflow {workflow_id} not found")
        return wf

    @staticmethod
    def _audit(
        workflow_id:  str,
        from_status:  Optional[WorkflowStatus],
        to_status:    WorkflowStatus,
        actor_id:     str,
        action:       str,
        notes:        str = "",
    ) -> WorkflowAuditEntry:
        return WorkflowAuditEntry(
            entry_id    = str(uuid.uuid4()),
            workflow_id = workflow_id,
            from_status = from_status,
            to_status   = to_status,
            actor_id    = actor_id,
            action      = action,
            notes       = notes,
        )


# ── Exceptions ─────────────────────────────────────────────────────────────────

class WorkflowError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_service: Optional[RegulatoryReviewWorkflow] = None


def get_review_workflow() -> RegulatoryReviewWorkflow:
    global _service
    if _service is None:
        _service = RegulatoryReviewWorkflow()
    return _service
