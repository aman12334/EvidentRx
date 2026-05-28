"""
Approval-gated workflow for learning system changes.

Any change to the learning system that could affect production behaviour
(calibration activation, prompt promotion, workflow version change,
experiment start) must pass through an explicit approval gate.

Approval design
───────────────
  - Every approvable change creates an ApprovalRequest
  - Requests require a minimum reviewer count (default: 1)
  - Self-approval is blocked (the requester cannot approve their own request)
  - Approved requests produce a signed ApprovalDecision with a content_hash
  - Expired requests (past deadline) are auto-rejected on next access
  - The audit trail records every decision with full context
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timedelta, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.learning.governance.approvals")

# Default approval request lifetime: 72 hours
_DEFAULT_EXPIRY_HOURS = 72


class ApprovalStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED  = "expired"
    CANCELLED= "cancelled"


class ChangeType(str, Enum):
    CALIBRATION_ACTIVATION  = "calibration_activation"
    PROMPT_PROMOTION        = "prompt_promotion"
    WORKFLOW_PROMOTION      = "workflow_promotion"
    EXPERIMENT_START        = "experiment_start"
    TEMPLATE_PROMOTION      = "template_promotion"
    THRESHOLD_ADJUSTMENT    = "threshold_adjustment"
    MEMORY_PURGE            = "memory_purge"


@dataclass
class ApprovalRequest:
    """
    A request for approval of a learning system change.

    Immutable once created (status is the only mutable field, via the
    ApprovalGate methods).
    """
    request_id:    str
    tenant_id:     str
    change_type:   ChangeType
    title:         str
    description:   str
    requested_by:  str
    artifact_id:   str           # ID of the object being approved (snapshot_id, etc.)
    change_payload: dict[str, Any]  # full context of the change
    status:        ApprovalStatus
    created_at:    datetime
    expires_at:    datetime
    min_approvers: int           = 1
    approvals:     list["ApprovalDecision"] = field(default_factory=list)
    rejections:    list["ApprovalDecision"] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        return datetime.now(tz=timezone.utc) > self.expires_at and self.status == ApprovalStatus.PENDING

    @property
    def approval_count(self) -> int:
        return len(self.approvals)

    @property
    def is_quorum_met(self) -> bool:
        return self.approval_count >= self.min_approvers

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id":   self.request_id,
            "tenant_id":    self.tenant_id,
            "change_type":  self.change_type.value,
            "title":        self.title,
            "artifact_id":  self.artifact_id,
            "status":       self.status.value,
            "requested_by": self.requested_by,
            "created_at":   self.created_at.isoformat(),
            "expires_at":   self.expires_at.isoformat(),
            "min_approvers":self.min_approvers,
            "approval_count":self.approval_count,
        }


@dataclass
class ApprovalDecision:
    """A single approval or rejection decision on a request."""
    decision_id:  str
    request_id:   str
    reviewer:     str
    decision:     str           # "approved" | "rejected"
    rationale:    str
    decided_at:   datetime
    content_hash: str           # tamper-evident hash of decision context

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id":  self.decision_id,
            "request_id":   self.request_id,
            "reviewer":     self.reviewer,
            "decision":     self.decision,
            "rationale":    self.rationale,
            "decided_at":   self.decided_at.isoformat(),
            "content_hash": self.content_hash,
        }


class ApprovalGate:
    """
    Manages approval requests for learning system changes.

    Enforces:
    - No self-approval
    - Expiry-based auto-rejection
    - Quorum requirement before marking APPROVED
    - Immutable decision records
    """

    def __init__(
        self,
        db_writer:     Optional[Callable] = None,
        expiry_hours:  int                = _DEFAULT_EXPIRY_HOURS,
    ) -> None:
        self._requests:   dict[str, ApprovalRequest] = {}
        self._by_artifact: dict[str, list[str]] = {}   # artifact_id → [request_ids]
        self._db_writer   = db_writer
        self._expiry_hours = expiry_hours

    # ── Create ─────────────────────────────────────────────────────────────────

    async def request_approval(
        self,
        tenant_id:      str,
        change_type:    ChangeType,
        title:          str,
        description:    str,
        requested_by:   str,
        artifact_id:    str,
        change_payload: dict[str, Any],
        min_approvers:  int = 1,
    ) -> ApprovalRequest:
        """Create a new approval request."""
        now = datetime.now(tz=timezone.utc)
        req = ApprovalRequest(
            request_id     = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            change_type    = change_type,
            title          = title,
            description    = description,
            requested_by   = requested_by,
            artifact_id    = artifact_id,
            change_payload = change_payload,
            status         = ApprovalStatus.PENDING,
            created_at     = now,
            expires_at     = now + timedelta(hours=self._expiry_hours),
            min_approvers  = min_approvers,
        )
        self._requests[req.request_id] = req
        self._by_artifact.setdefault(artifact_id, []).append(req.request_id)
        await self._persist("create_request", req)
        log.info(
            "ApprovalGate: created %s request [%s] by %s",
            change_type.value, req.request_id[:8], requested_by,
        )
        return req

    # ── Decision ───────────────────────────────────────────────────────────────

    async def approve(
        self,
        request_id: str,
        reviewer:   str,
        rationale:  str = "",
    ) -> ApprovalDecision:
        """Record an approval decision."""
        req = self._get_active(request_id)

        if reviewer == req.requested_by:
            raise ApprovalError("Self-approval is not permitted")
        if any(d.reviewer == reviewer for d in req.approvals):
            raise ApprovalError(f"{reviewer} has already approved this request")

        decision = _make_decision(request_id, reviewer, "approved", rationale)
        req.approvals.append(decision)

        if req.is_quorum_met:
            req.status = ApprovalStatus.APPROVED
            log.info(
                "ApprovalGate: request %s APPROVED (quorum=%d)",
                request_id[:8], req.approval_count,
            )

        await self._persist("update_request", req)
        await self._persist("create_decision", decision)
        return decision

    async def reject(
        self,
        request_id: str,
        reviewer:   str,
        rationale:  str,
    ) -> ApprovalDecision:
        """Reject an approval request (any one rejection blocks the change)."""
        req = self._get_active(request_id)

        decision = _make_decision(request_id, reviewer, "rejected", rationale)
        req.rejections.append(decision)
        req.status = ApprovalStatus.REJECTED

        log.info(
            "ApprovalGate: request %s REJECTED by %s",
            request_id[:8], reviewer,
        )
        await self._persist("update_request", req)
        await self._persist("create_decision", decision)
        return decision

    async def cancel(self, request_id: str, cancelled_by: str) -> ApprovalRequest:
        """Cancel a pending request (only the requester can cancel)."""
        req = self._requests.get(request_id)
        if req is None:
            raise ApprovalNotFoundError(request_id)
        if req.status != ApprovalStatus.PENDING:
            raise ApprovalError(f"Request {request_id[:8]} is not PENDING")
        if cancelled_by != req.requested_by:
            raise ApprovalError("Only the original requester can cancel an approval request")
        req.status = ApprovalStatus.CANCELLED
        await self._persist("update_request", req)
        return req

    # ── Queries ────────────────────────────────────────────────────────────────

    def get(self, request_id: str) -> Optional[ApprovalRequest]:
        req = self._requests.get(request_id)
        if req and req.is_expired:
            req.status = ApprovalStatus.EXPIRED
        return req

    def get_for_artifact(self, artifact_id: str) -> list[ApprovalRequest]:
        ids = self._by_artifact.get(artifact_id, [])
        return [self._requests[i] for i in ids if i in self._requests]

    def list_pending(
        self,
        tenant_id:   str,
        change_type: Optional[ChangeType] = None,
    ) -> list[ApprovalRequest]:
        result = []
        for req in self._requests.values():
            if req.tenant_id != tenant_id:
                continue
            # Check expiry
            if req.is_expired:
                req.status = ApprovalStatus.EXPIRED
                continue
            if req.status != ApprovalStatus.PENDING:
                continue
            if change_type and req.change_type != change_type:
                continue
            result.append(req)
        return sorted(result, key=lambda r: r.created_at)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_active(self, request_id: str) -> ApprovalRequest:
        req = self._requests.get(request_id)
        if req is None:
            raise ApprovalNotFoundError(request_id)
        if req.is_expired:
            req.status = ApprovalStatus.EXPIRED
            raise ApprovalError(f"Request {request_id[:8]} has expired")
        if req.status != ApprovalStatus.PENDING:
            raise ApprovalError(f"Request {request_id[:8]} is {req.status.value}")
        return req

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("ApprovalGate: persist failed: %s", exc)


# ── Factory helpers ────────────────────────────────────────────────────────────

def _make_decision(
    request_id: str,
    reviewer:   str,
    decision:   str,
    rationale:  str,
) -> ApprovalDecision:
    now = datetime.now(tz=timezone.utc)
    payload = json.dumps(
        {"request_id": request_id, "reviewer": reviewer,
         "decision": decision, "decided_at": now.isoformat()},
        sort_keys=True,
    ).encode()
    content_hash = hashlib.sha256(payload).hexdigest()
    return ApprovalDecision(
        decision_id  = str(uuid.uuid4()),
        request_id   = request_id,
        reviewer     = reviewer,
        decision     = decision,
        rationale    = rationale,
        decided_at   = now,
        content_hash = content_hash,
    )


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ApprovalNotFoundError(Exception):
    pass

class ApprovalError(Exception):
    pass


# ── Module-level singleton ─────────────────────────────────────────────────────

_gate: Optional[ApprovalGate] = None


def get_approval_gate(
    db_writer:    Optional[Callable] = None,
    expiry_hours: int                = _DEFAULT_EXPIRY_HOURS,
) -> ApprovalGate:
    global _gate
    if _gate is None:
        _gate = ApprovalGate(db_writer=db_writer, expiry_hours=expiry_hours)
    return _gate
