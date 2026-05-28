"""
Workflow version registry.

Maintains immutable, versioned workflow definitions used by the investigation
orchestration layer. A workflow definition describes the sequence of agent
steps, decision points, escalation rules, and output contracts for one
investigation workflow type.

Workflow lifecycle
──────────────────
  DRAFT → REVIEW → ACTIVE (one active per slot)
        ↘ REJECTED
  ACTIVE → DEPRECATED (on supersession)
  ACTIVE/DEPRECATED → ACTIVE (rollback)

Immutability guarantee
──────────────────────
  Once a WorkflowVersion enters REVIEW, its step definitions, routing
  logic, and output contracts are frozen. The content_hash provides
  tamper-evidence. Any modification creates a new DRAFT version.
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

log = logging.getLogger("evidentrx.learning.versioning.workflow_registry")


class WorkflowStatus(str, Enum):
    DRAFT      = "draft"
    REVIEW     = "review"
    ACTIVE     = "active"
    DEPRECATED = "deprecated"
    REJECTED   = "rejected"


class WorkflowType(str, Enum):
    """Recognised workflow slot types."""
    INITIAL_INVESTIGATION     = "initial_investigation"
    ESCALATION_REVIEW         = "escalation_review"
    FALSE_POSITIVE_REVIEW     = "false_positive_review"
    REMEDIATION_PLANNING      = "remediation_planning"
    RISK_ASSESSMENT           = "risk_assessment"
    REGULATORY_REVIEW         = "regulatory_review"
    CASE_CLOSURE              = "case_closure"
    CONTINUOUS_MONITORING     = "continuous_monitoring"


@dataclass
class WorkflowStep:
    """A single step within a workflow definition."""
    step_id:      str
    name:         str
    step_type:    str           # "agent" | "human_review" | "decision" | "parallel" | "end"
    prompt_slot:  Optional[str] = None    # PromptSlot name used by agent steps
    conditions:   dict[str, Any] = field(default_factory=dict)  # routing conditions
    timeout_seconds: Optional[int] = None
    required:     bool           = True


@dataclass
class WorkflowVersion:
    """
    Immutable versioned workflow definition.

    The steps list defines the directed execution graph. Once submitted
    for review the graph is frozen — changes require a new version.
    """
    workflow_id:       str
    tenant_id:         str
    workflow_name:     str          # WorkflowType value or custom
    version:           str          # semantic: "major.minor.patch"
    title:             str
    description:       str
    steps:             list[WorkflowStep]
    output_contract:   dict[str, Any]  # expected output schema (JSON Schema subset)
    status:            WorkflowStatus
    content_hash:      str
    created_at:        datetime
    created_by:        str
    change_summary:    str              = ""
    approved_by:       Optional[str]    = None
    approved_at:       Optional[datetime] = None
    parent_version_id: Optional[str]   = None
    min_agent_version: str              = "1.0.0"  # minimum compatible agent SDK version
    metadata:          dict[str, Any]  = field(default_factory=dict)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def has_human_review(self) -> bool:
        return any(s.step_type == "human_review" for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id":       self.workflow_id,
            "tenant_id":         self.tenant_id,
            "workflow_name":     self.workflow_name,
            "version":           self.version,
            "title":             self.title,
            "description":       self.description,
            "step_count":        self.step_count,
            "has_human_review":  self.has_human_review,
            "status":            self.status.value,
            "content_hash":      self.content_hash,
            "created_at":        self.created_at.isoformat(),
            "created_by":        self.created_by,
            "change_summary":    self.change_summary,
            "approved_by":       self.approved_by,
            "approved_at":       self.approved_at.isoformat() if self.approved_at else None,
            "parent_version_id": self.parent_version_id,
            "min_agent_version": self.min_agent_version,
        }


class WorkflowRegistry:
    """
    Governed registry of versioned workflow definitions.

    Maintains the full version history and exposes the currently active
    definition per workflow slot. All promotions require explicit approval.
    """

    def __init__(self, db_writer: Optional[Callable] = None) -> None:
        self._workflows: dict[str, WorkflowVersion] = {}
        # (tenant_id, workflow_name) → workflow_id of ACTIVE version
        self._active:   dict[tuple[str, str], str] = {}
        self._db_writer = db_writer

    # ── Create ─────────────────────────────────────────────────────────────────

    async def register(
        self,
        tenant_id:        str,
        workflow_name:    str,
        version:          str,
        title:            str,
        description:      str,
        steps:            list[WorkflowStep],
        output_contract:  dict[str, Any],
        created_by:       str,
        change_summary:   str             = "",
        parent_version_id: Optional[str] = None,
        min_agent_version: str            = "1.0.0",
        metadata:         Optional[dict] = None,
    ) -> WorkflowVersion:
        """Register a new workflow version in DRAFT status."""
        content_hash = _hash_workflow(steps, output_contract)

        wv = WorkflowVersion(
            workflow_id       = str(uuid.uuid4()),
            tenant_id         = tenant_id,
            workflow_name     = workflow_name,
            version           = version,
            title             = title,
            description       = description,
            steps             = steps,
            output_contract   = output_contract,
            status            = WorkflowStatus.DRAFT,
            content_hash      = content_hash,
            created_at        = datetime.now(tz=timezone.utc),
            created_by        = created_by,
            change_summary    = change_summary,
            parent_version_id = parent_version_id,
            min_agent_version = min_agent_version,
            metadata          = metadata or {},
        )
        self._workflows[wv.workflow_id] = wv
        await self._persist("create", wv)
        log.info(
            "WorkflowRegistry: registered %s v%s [%s] by %s",
            workflow_name, version, wv.workflow_id[:8], created_by,
        )
        return wv

    # ── Lifecycle transitions ──────────────────────────────────────────────────

    async def submit_for_review(self, workflow_id: str) -> WorkflowVersion:
        wv = self._require(workflow_id, WorkflowStatus.DRAFT)
        if wv.step_count == 0:
            raise WorkflowRegistryError("Cannot submit a workflow with no steps for review")
        wv.status = WorkflowStatus.REVIEW
        await self._persist("update", wv)
        log.info("WorkflowRegistry: %s v%s submitted for review", wv.workflow_name, wv.version)
        return wv

    async def approve(
        self,
        workflow_id: str,
        approved_by: str,
    ) -> WorkflowVersion:
        """
        Approve a workflow version and make it ACTIVE.

        Deprecates the current active version for the same slot.
        """
        wv = self._require(workflow_id, WorkflowStatus.REVIEW)
        wv.status      = WorkflowStatus.ACTIVE
        wv.approved_by = approved_by
        wv.approved_at = datetime.now(tz=timezone.utc)

        key = (wv.tenant_id, wv.workflow_name)
        current_id = self._active.get(key)
        if current_id and current_id != workflow_id:
            current = self._workflows.get(current_id)
            if current and current.status == WorkflowStatus.ACTIVE:
                current.status = WorkflowStatus.DEPRECATED
                await self._persist("update", current)

        self._active[key] = workflow_id
        await self._persist("update", wv)
        log.info(
            "WorkflowRegistry: %s v%s APPROVED by %s",
            wv.workflow_name, wv.version, approved_by,
        )
        return wv

    async def reject(
        self,
        workflow_id:  str,
        rejected_by:  str,
        reason:       str,
    ) -> WorkflowVersion:
        wv = self._require(workflow_id, WorkflowStatus.REVIEW)
        wv.status = WorkflowStatus.REJECTED
        wv.metadata["rejection_reason"] = reason
        wv.metadata["rejected_by"]      = rejected_by
        wv.metadata["rejected_at"]      = datetime.now(tz=timezone.utc).isoformat()
        await self._persist("update", wv)
        return wv

    async def rollback(
        self,
        tenant_id:      str,
        workflow_name:  str,
        target_version: str,
        rolled_by:      str,
    ) -> WorkflowVersion:
        """
        Roll back to a prior ACTIVE or DEPRECATED workflow version.

        Follows the same approval path — the rollback initiator becomes
        the approver, creating an auditable record.
        """
        target = next(
            (wv for wv in self._workflows.values()
             if wv.tenant_id == tenant_id
             and wv.workflow_name == workflow_name
             and wv.version == target_version
             and wv.status in (WorkflowStatus.ACTIVE, WorkflowStatus.DEPRECATED)),
            None,
        )
        if target is None:
            raise WorkflowNotFoundError(
                f"No workflow {workflow_name} v{target_version} for tenant {tenant_id}"
            )

        target.status        = WorkflowStatus.REVIEW
        target.approved_by   = None
        target.approved_at   = None
        target.metadata["rolled_back_by"] = rolled_by
        target.metadata["rolled_back_at"] = datetime.now(tz=timezone.utc).isoformat()

        log.info(
            "WorkflowRegistry: rollback %s to v%s by %s",
            workflow_name, target_version, rolled_by,
        )
        return await self.approve(target.workflow_id, approved_by=rolled_by)

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def snapshot(
        self,
        tenant_id: str,
        workflow_name: str,
    ) -> Optional[dict[str, Any]]:
        """
        Return the full active workflow snapshot for reproducibility.

        Used by the evaluation harness to pin a run to an exact workflow
        definition — provides the workflow_id + content_hash for later
        verification.
        """
        wv = self.get_active(tenant_id, workflow_name)
        if wv is None:
            return None
        return {
            "workflow_id":   wv.workflow_id,
            "workflow_name": wv.workflow_name,
            "version":       wv.version,
            "content_hash":  wv.content_hash,
            "snapshotted_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_active(
        self,
        tenant_id:     str,
        workflow_name: str,
    ) -> Optional[WorkflowVersion]:
        key = (tenant_id, workflow_name)
        wid = self._active.get(key)
        return self._workflows.get(wid) if wid else None

    def get(self, workflow_id: str) -> Optional[WorkflowVersion]:
        return self._workflows.get(workflow_id)

    def list_versions(
        self,
        tenant_id:     str,
        workflow_name: Optional[str]       = None,
        status:        Optional[WorkflowStatus] = None,
    ) -> list[WorkflowVersion]:
        result = [
            wv for wv in self._workflows.values()
            if wv.tenant_id == tenant_id
            and (workflow_name is None or wv.workflow_name == workflow_name)
            and (status is None or wv.status == status)
        ]
        return sorted(result, key=lambda wv: wv.created_at, reverse=True)

    def lineage(self, workflow_id: str) -> list[WorkflowVersion]:
        """Return the full ancestor chain, oldest first."""
        chain: list[WorkflowVersion] = []
        current = self._workflows.get(workflow_id)
        visited: set[str] = set()
        while current is not None and current.workflow_id not in visited:
            chain.append(current)
            visited.add(current.workflow_id)
            parent_id = current.parent_version_id
            current   = self._workflows.get(parent_id) if parent_id else None
        chain.reverse()
        return chain

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _require(self, workflow_id: str, expected: WorkflowStatus) -> WorkflowVersion:
        wv = self._workflows.get(workflow_id)
        if wv is None:
            raise WorkflowNotFoundError(workflow_id)
        if wv.status != expected:
            raise WorkflowRegistryError(
                f"Workflow {workflow_id[:8]} is {wv.status.value}, expected {expected.value}"
            )
        return wv

    async def _persist(self, op: str, wv: WorkflowVersion) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, wv)
            except Exception as exc:
                log.error("WorkflowRegistry: persist failed: %s", exc)


# ── Hash helper ────────────────────────────────────────────────────────────────

def _hash_workflow(
    steps:           list[WorkflowStep],
    output_contract: dict[str, Any],
) -> str:
    steps_repr = [
        {
            "step_id":      s.step_id,
            "name":         s.name,
            "step_type":    s.step_type,
            "prompt_slot":  s.prompt_slot,
            "conditions":   s.conditions,
            "required":     s.required,
        }
        for s in steps
    ]
    payload = json.dumps(
        {"steps": steps_repr, "output_contract": output_contract},
        sort_keys=True, default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


# ── Exceptions ─────────────────────────────────────────────────────────────────

class WorkflowNotFoundError(Exception):
    pass

class WorkflowRegistryError(Exception):
    pass


# ── Module-level singleton ─────────────────────────────────────────────────────

_registry: Optional[WorkflowRegistry] = None


def get_workflow_registry(db_writer: Optional[Callable] = None) -> WorkflowRegistry:
    global _registry
    if _registry is None:
        _registry = WorkflowRegistry(db_writer=db_writer)
    return _registry
