"""
Immutable governance audit trail for the learning system.

Every significant action in the learning layer — feedback submissions,
calibration changes, prompt promotions, experiment decisions — produces
an immutable audit record. Records are append-only and content-hashed
for tamper detection.

Audit design
────────────
  - Append-only: records are never modified or deleted within retention
  - Content-hashed: each record's payload is SHA-256 hashed for integrity
  - Chain-linked: each record references the prior record's hash, forming
    a cryptographic audit chain per tenant
  - Actor-attributed: every record captures who performed the action
  - PHI-free: no patient identifiers; only platform entity IDs
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.learning.governance.audit")


class LearningAuditEventType(str, Enum):
    # Feedback
    FEEDBACK_SUBMITTED       = "feedback_submitted"
    FEEDBACK_ACCEPTED        = "feedback_accepted"
    FEEDBACK_REJECTED        = "feedback_rejected"
    # Calibration
    CALIBRATION_CREATED      = "calibration_created"
    CALIBRATION_APPROVED     = "calibration_approved"
    CALIBRATION_ACTIVATED    = "calibration_activated"
    CALIBRATION_ROLLED_BACK  = "calibration_rolled_back"
    # Prompt / Workflow versioning
    PROMPT_REGISTERED        = "prompt_registered"
    PROMPT_PROMOTED          = "prompt_promoted"
    PROMPT_ROLLED_BACK       = "prompt_rolled_back"
    WORKFLOW_REGISTERED      = "workflow_registered"
    WORKFLOW_PROMOTED        = "workflow_promoted"
    WORKFLOW_ROLLED_BACK     = "workflow_rolled_back"
    # Recommendations
    TEMPLATE_PROMOTED        = "template_promoted"
    TEMPLATE_ROLLED_BACK     = "template_rolled_back"
    # Experiments
    EXPERIMENT_CREATED       = "experiment_created"
    EXPERIMENT_STARTED       = "experiment_started"
    EXPERIMENT_COMPLETED     = "experiment_completed"
    EXPERIMENT_CANCELLED     = "experiment_cancelled"
    # Approvals
    APPROVAL_REQUESTED       = "approval_requested"
    APPROVAL_GRANTED         = "approval_granted"
    APPROVAL_REJECTED        = "approval_rejected"
    # Memory
    MEMORY_PURGE_INITIATED   = "memory_purge_initiated"
    # Policy
    POLICY_VIOLATION         = "policy_violation"
    ACCESS_DENIED            = "access_denied"


@dataclass
class LearningAuditRecord:
    """
    A single immutable audit record for the learning system.

    The chain_hash links this record to the previous one for the same
    tenant, forming a verifiable audit chain. Tampering with any record
    breaks the chain hash of all subsequent records.
    """
    audit_id:    str
    tenant_id:   str
    event_type:  LearningAuditEventType
    actor:       str              # analyst_id, system account, or "system"
    artifact_id: Optional[str]   # case_id, snapshot_id, prompt_id, etc.
    artifact_type: Optional[str] # "calibration_snapshot" | "prompt" | "experiment" | …
    payload:     dict[str, Any]  # event-specific context (PHI-free)
    occurred_at: datetime
    content_hash: str            # SHA-256 of payload
    prior_hash:  Optional[str]   # chain link to previous record
    chain_hash:  str             # SHA-256(prior_hash + content_hash)
    source_ip:   Optional[str]   = None
    session_id:  Optional[str]   = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id":     self.audit_id,
            "tenant_id":    self.tenant_id,
            "event_type":   self.event_type.value,
            "actor":        self.actor,
            "artifact_id":  self.artifact_id,
            "artifact_type":self.artifact_type,
            "occurred_at":  self.occurred_at.isoformat(),
            "content_hash": self.content_hash,
            "chain_hash":   self.chain_hash,
        }


class LearningAuditLog:
    """
    Append-only audit log for the learning governance layer.

    Maintains a per-tenant chain for tamper detection. The chain can be
    verified at any time with verify_chain().
    """

    def __init__(self, db_writer: Optional[Callable] = None) -> None:
        self._records:    list[LearningAuditRecord] = []
        self._by_tenant:  dict[str, list[str]] = {}   # tenant_id → [audit_ids]
        self._last_hash:  dict[str, Optional[str]] = {}  # tenant_id → last chain_hash
        self._db_writer   = db_writer
        self._buffer:     list[LearningAuditRecord] = []

    # ── Log ────────────────────────────────────────────────────────────────────

    async def log(
        self,
        tenant_id:     str,
        event_type:    LearningAuditEventType,
        actor:         str,
        payload:       dict[str, Any],
        artifact_id:   Optional[str]  = None,
        artifact_type: Optional[str]  = None,
        source_ip:     Optional[str]  = None,
        session_id:    Optional[str]  = None,
    ) -> LearningAuditRecord:
        now          = datetime.now(tz=timezone.utc)
        content_hash = _hash_payload(payload)
        prior_hash   = self._last_hash.get(tenant_id)
        chain_hash   = _chain_hash(prior_hash, content_hash)

        record = LearningAuditRecord(
            audit_id      = str(uuid.uuid4()),
            tenant_id     = tenant_id,
            event_type    = event_type,
            actor         = actor,
            artifact_id   = artifact_id,
            artifact_type = artifact_type,
            payload       = payload,
            occurred_at   = now,
            content_hash  = content_hash,
            prior_hash    = prior_hash,
            chain_hash    = chain_hash,
            source_ip     = source_ip,
            session_id    = session_id,
        )

        self._records.append(record)
        self._by_tenant.setdefault(tenant_id, []).append(record.audit_id)
        self._last_hash[tenant_id] = chain_hash
        self._buffer.append(record)

        if len(self._buffer) >= 50:
            await self.flush()

        return record

    async def flush(self) -> None:
        if not self._buffer or not self._db_writer:
            self._buffer.clear()
            return
        try:
            for record in self._buffer:
                await self._db_writer("create", record)
        except Exception as exc:
            log.error("LearningAuditLog: flush failed: %s", exc)
        finally:
            self._buffer.clear()

    # ── Convenience loggers ────────────────────────────────────────────────────

    async def log_feedback(
        self,
        tenant_id:   str,
        actor:       str,
        feedback_id: str,
        feedback_type: str,
        accepted:    bool,
    ) -> LearningAuditRecord:
        event_type = (
            LearningAuditEventType.FEEDBACK_ACCEPTED if accepted
            else LearningAuditEventType.FEEDBACK_REJECTED
        )
        return await self.log(
            tenant_id     = tenant_id,
            event_type    = event_type,
            actor         = actor,
            payload       = {"feedback_id": feedback_id, "feedback_type": feedback_type},
            artifact_id   = feedback_id,
            artifact_type = "feedback",
        )

    async def log_calibration_activated(
        self,
        tenant_id:   str,
        actor:       str,
        snapshot_id: str,
        version:     str,
    ) -> LearningAuditRecord:
        return await self.log(
            tenant_id     = tenant_id,
            event_type    = LearningAuditEventType.CALIBRATION_ACTIVATED,
            actor         = actor,
            payload       = {"snapshot_id": snapshot_id, "version": version},
            artifact_id   = snapshot_id,
            artifact_type = "calibration_snapshot",
        )

    async def log_prompt_promoted(
        self,
        tenant_id:    str,
        actor:        str,
        prompt_id:    str,
        prompt_name:  str,
        version:      str,
    ) -> LearningAuditRecord:
        return await self.log(
            tenant_id     = tenant_id,
            event_type    = LearningAuditEventType.PROMPT_PROMOTED,
            actor         = actor,
            payload       = {"prompt_id": prompt_id, "prompt_name": prompt_name, "version": version},
            artifact_id   = prompt_id,
            artifact_type = "prompt",
        )

    async def log_experiment_decision(
        self,
        tenant_id:     str,
        actor:         str,
        experiment_id: str,
        decision:      str,   # "started" | "completed" | "cancelled"
    ) -> LearningAuditRecord:
        event_map = {
            "started":   LearningAuditEventType.EXPERIMENT_STARTED,
            "completed": LearningAuditEventType.EXPERIMENT_COMPLETED,
            "cancelled": LearningAuditEventType.EXPERIMENT_CANCELLED,
        }
        return await self.log(
            tenant_id     = tenant_id,
            event_type    = event_map.get(decision, LearningAuditEventType.EXPERIMENT_STARTED),
            actor         = actor,
            payload       = {"experiment_id": experiment_id, "decision": decision},
            artifact_id   = experiment_id,
            artifact_type = "experiment",
        )

    async def log_policy_violation(
        self,
        tenant_id:   str,
        actor:       str,
        policy_name: str,
        artifact_id: Optional[str],
        reason:      str,
    ) -> LearningAuditRecord:
        return await self.log(
            tenant_id     = tenant_id,
            event_type    = LearningAuditEventType.POLICY_VIOLATION,
            actor         = actor,
            payload       = {"policy_name": policy_name, "reason": reason},
            artifact_id   = artifact_id,
            artifact_type = "policy",
        )

    # ── Chain verification ─────────────────────────────────────────────────────

    def verify_chain(self, tenant_id: str) -> tuple[bool, list[str]]:
        """
        Verify the integrity of the audit chain for a tenant.

        Returns (is_valid, list_of_violations). An empty violations list
        means the chain is intact.
        """
        violations: list[str] = []
        ids     = self._by_tenant.get(tenant_id, [])
        records = [r for r in self._records if r.audit_id in ids]
        records.sort(key=lambda r: r.occurred_at)

        prior_hash: Optional[str] = None
        for rec in records:
            # Recompute chain hash
            expected_chain = _chain_hash(prior_hash, rec.content_hash)
            if rec.chain_hash != expected_chain:
                violations.append(
                    f"Chain broken at audit_id={rec.audit_id[:8]} "
                    f"(expected={expected_chain[:12]}, got={rec.chain_hash[:12]})"
                )
            prior_hash = rec.chain_hash

        return len(violations) == 0, violations

    # ── Query ──────────────────────────────────────────────────────────────────

    def query(
        self,
        tenant_id:    str,
        event_type:   Optional[LearningAuditEventType] = None,
        actor:        Optional[str]                    = None,
        artifact_id:  Optional[str]                    = None,
        since:        Optional[datetime]               = None,
        limit:        int                              = 200,
    ) -> list[LearningAuditRecord]:
        result = [
            r for r in self._records
            if r.tenant_id == tenant_id
            and (event_type is None or r.event_type == event_type)
            and (actor is None or r.actor == actor)
            and (artifact_id is None or r.artifact_id == artifact_id)
            and (since is None or r.occurred_at >= since)
        ]
        return sorted(result, key=lambda r: r.occurred_at, reverse=True)[:limit]


# ── Crypto helpers ─────────────────────────────────────────────────────────────

def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _chain_hash(prior_hash: Optional[str], content_hash: str) -> str:
    combined = (prior_hash or "") + content_hash
    return hashlib.sha256(combined.encode()).hexdigest()


# ── Module-level singleton ─────────────────────────────────────────────────────

_audit_log: Optional[LearningAuditLog] = None


def get_learning_audit_log(db_writer: Optional[Callable] = None) -> LearningAuditLog:
    global _audit_log
    if _audit_log is None:
        _audit_log = LearningAuditLog(db_writer=db_writer)
    return _audit_log
