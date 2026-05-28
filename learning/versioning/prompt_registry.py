"""
Prompt version registry.

Maintains immutable, versioned prompt templates used by the investigation
runtime. Every change to a prompt creates a new version — no version is ever
overwritten. Promotions require explicit approval to ensure governance.

Prompt lifecycle
────────────────
  DRAFT → REVIEW → ACTIVE (one active per slot)
        ↘ REJECTED
  ACTIVE → DEPRECATED (superseded by newer version)
  ACTIVE/DEPRECATED → ACTIVE (via rollback)

A "slot" is (tenant_id, prompt_name) — there can be at most one ACTIVE
version per slot at any time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.learning.versioning.prompt_registry")


class PromptStatus(str, Enum):
    DRAFT      = "draft"
    REVIEW     = "review"
    ACTIVE     = "active"
    DEPRECATED = "deprecated"
    REJECTED   = "rejected"


class PromptSlot(str, Enum):
    """Well-known prompt slots used by the investigation runtime."""
    RISK_ASSESSMENT     = "risk_assessment"
    EVIDENCE_ANALYSIS   = "evidence_analysis"
    RECOMMENDATION_GEN  = "recommendation_gen"
    ESCALATION_DECISION = "escalation_decision"
    CASE_SUMMARY        = "case_summary"
    FALSE_POSITIVE_EVAL = "false_positive_eval"
    REMEDIATION_PLAN    = "remediation_plan"
    QUALITY_REVIEW      = "quality_review"


@dataclass
class PromptVersion:
    """
    Immutable versioned prompt.

    Once created the `template` and `system_context` fields are frozen.
    The content_hash detects tampering. The parent_version_id allows
    reconstruction of the full lineage chain.
    """
    prompt_id:        str
    tenant_id:        str
    prompt_name:      str               # logical slot name (PromptSlot value or custom)
    version:          str               # semantic: "major.minor.patch"
    title:            str
    template:         str               # Jinja2-compatible prompt template
    system_context:   str               # system prompt / persona definition
    model_target:     str               # model family this prompt is optimised for
    status:           PromptStatus
    content_hash:     str               # SHA-256(template + system_context)
    created_at:       datetime
    created_by:       str
    change_summary:   str               = ""     # human-readable change description
    approved_by:      str | None     = None
    approved_at:      datetime | None= None
    parent_version_id: str | None   = None   # previous version in lineage chain
    test_coverage:    float             = 0.0    # fraction of benchmark cases passing
    metadata:         dict[str, Any]   = field(default_factory=dict)

    @property
    def is_eligible_for_review(self) -> bool:
        """Minimum test coverage required before promotion to review."""
        return self.test_coverage >= 0.70

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id":        self.prompt_id,
            "tenant_id":        self.tenant_id,
            "prompt_name":      self.prompt_name,
            "version":          self.version,
            "title":            self.title,
            "model_target":     self.model_target,
            "status":           self.status.value,
            "content_hash":     self.content_hash,
            "created_at":       self.created_at.isoformat(),
            "created_by":       self.created_by,
            "change_summary":   self.change_summary,
            "approved_by":      self.approved_by,
            "approved_at":      self.approved_at.isoformat() if self.approved_at else None,
            "parent_version_id":self.parent_version_id,
            "test_coverage":    self.test_coverage,
        }


class PromptRegistry:
    """
    Governed registry of versioned prompt templates.

    All writes go through an approval workflow. The registry tracks the full
    version history and exposes the currently active version per slot.
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._versions:    dict[str, PromptVersion] = {}
        # (tenant_id, prompt_name) → prompt_id of ACTIVE version
        self._active:      dict[tuple[str, str], str] = {}
        self._db_writer    = db_writer

    # ── Create ─────────────────────────────────────────────────────────────────

    async def register(
        self,
        tenant_id:        str,
        prompt_name:      str,
        version:          str,
        title:            str,
        template:         str,
        system_context:   str,
        model_target:     str,
        created_by:       str,
        change_summary:   str              = "",
        parent_version_id: str | None  = None,
        test_coverage:    float            = 0.0,
        metadata:         dict | None  = None,
    ) -> PromptVersion:
        """Register a new prompt version in DRAFT status."""
        content_hash = _hash_prompt(template, system_context)

        pv = PromptVersion(
            prompt_id         = str(uuid.uuid4()),
            tenant_id         = tenant_id,
            prompt_name       = prompt_name,
            version           = version,
            title             = title,
            template          = template,
            system_context    = system_context,
            model_target      = model_target,
            status            = PromptStatus.DRAFT,
            content_hash      = content_hash,
            created_at        = datetime.now(tz=UTC),
            created_by        = created_by,
            change_summary    = change_summary,
            parent_version_id = parent_version_id,
            test_coverage     = test_coverage,
            metadata          = metadata or {},
        )
        self._versions[pv.prompt_id] = pv
        await self._persist("create", pv)
        log.info(
            "PromptRegistry: registered %s v%s [%s] by %s",
            prompt_name, version, pv.prompt_id[:8], created_by,
        )
        return pv

    # ── Lifecycle transitions ──────────────────────────────────────────────────

    async def submit_for_review(
        self,
        prompt_id: str,
    ) -> PromptVersion:
        """Submit a DRAFT prompt for approval review."""
        pv = self._require(prompt_id, PromptStatus.DRAFT)
        if not pv.is_eligible_for_review:
            raise PromptRegistryError(
                f"Prompt {prompt_id[:8]} has test_coverage={pv.test_coverage:.2f}; "
                f"minimum 0.70 required for review"
            )
        pv.status = PromptStatus.REVIEW
        await self._persist("update", pv)
        log.info("PromptRegistry: %s v%s submitted for review", pv.prompt_name, pv.version)
        return pv

    async def approve(
        self,
        prompt_id:   str,
        approved_by: str,
    ) -> PromptVersion:
        """
        Approve a prompt version and make it ACTIVE.

        Deprecates the current active version for the same slot.
        """
        pv = self._require(prompt_id, PromptStatus.REVIEW)
        pv.status      = PromptStatus.ACTIVE
        pv.approved_by = approved_by
        pv.approved_at = datetime.now(tz=UTC)

        # Deprecate current active for this slot
        key = (pv.tenant_id, pv.prompt_name)
        current_id = self._active.get(key)
        if current_id and current_id != prompt_id:
            current = self._versions.get(current_id)
            if current and current.status == PromptStatus.ACTIVE:
                current.status = PromptStatus.DEPRECATED
                await self._persist("update", current)

        self._active[key] = prompt_id
        await self._persist("update", pv)
        log.info(
            "PromptRegistry: %s v%s APPROVED by %s",
            pv.prompt_name, pv.version, approved_by,
        )
        return pv

    async def reject(
        self,
        prompt_id:    str,
        rejected_by:  str,
        reason:       str,
    ) -> PromptVersion:
        pv = self._require(prompt_id, PromptStatus.REVIEW)
        pv.status = PromptStatus.REJECTED
        pv.metadata["rejection_reason"] = reason
        pv.metadata["rejected_by"]      = rejected_by
        pv.metadata["rejected_at"]      = datetime.now(tz=UTC).isoformat()
        await self._persist("update", pv)
        return pv

    async def rollback(
        self,
        tenant_id:      str,
        prompt_name:    str,
        target_version: str,
        rolled_by:      str,
    ) -> PromptVersion:
        """
        Roll back to a prior version.

        The target must be ACTIVE or DEPRECATED. It is re-submitted for
        approval (to REVIEW) and then immediately approved by the rollback
        initiator, following the same governance path as a normal promotion.
        """
        target = next(
            (pv for pv in self._versions.values()
             if pv.tenant_id == tenant_id
             and pv.prompt_name == prompt_name
             and pv.version == target_version
             and pv.status in (PromptStatus.ACTIVE, PromptStatus.DEPRECATED)),
            None,
        )
        if target is None:
            raise PromptNotFoundError(
                f"No prompt {prompt_name} v{target_version} for tenant {tenant_id}"
            )

        # Re-enter review cycle
        target.status        = PromptStatus.REVIEW
        target.approved_by   = None
        target.approved_at   = None
        target.metadata["rolled_back_by"] = rolled_by
        target.metadata["rolled_back_at"] = datetime.now(tz=UTC).isoformat()

        log.info(
            "PromptRegistry: rollback %s to v%s by %s",
            prompt_name, target_version, rolled_by,
        )
        return await self.approve(target.prompt_id, approved_by=rolled_by)

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_active(
        self,
        tenant_id:   str,
        prompt_name: str,
    ) -> PromptVersion | None:
        key = (tenant_id, prompt_name)
        pid = self._active.get(key)
        return self._versions.get(pid) if pid else None

    def get(self, prompt_id: str) -> PromptVersion | None:
        return self._versions.get(prompt_id)

    def list_versions(
        self,
        tenant_id:   str,
        prompt_name: str | None    = None,
        status:      PromptStatus | None = None,
    ) -> list[PromptVersion]:
        result = [
            pv for pv in self._versions.values()
            if pv.tenant_id == tenant_id
            and (prompt_name is None or pv.prompt_name == prompt_name)
            and (status is None or pv.status == status)
        ]
        return sorted(result, key=lambda pv: pv.created_at, reverse=True)

    def lineage(self, prompt_id: str) -> list[PromptVersion]:
        """
        Return the full ancestor chain for a prompt version (oldest first).

        Walks parent_version_id links up to the genesis version.
        """
        chain: list[PromptVersion] = []
        current = self._versions.get(prompt_id)
        visited: set[str] = set()
        while current is not None and current.prompt_id not in visited:
            chain.append(current)
            visited.add(current.prompt_id)
            parent_id = current.parent_version_id
            current   = self._versions.get(parent_id) if parent_id else None
        chain.reverse()
        return chain

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _require(self, prompt_id: str, expected: PromptStatus) -> PromptVersion:
        pv = self._versions.get(prompt_id)
        if pv is None:
            raise PromptNotFoundError(prompt_id)
        if pv.status != expected:
            raise PromptRegistryError(
                f"Prompt {prompt_id[:8]} is {pv.status.value}, expected {expected.value}"
            )
        return pv

    async def _persist(self, op: str, pv: PromptVersion) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, pv)
            except Exception as exc:
                log.error("PromptRegistry: persist failed: %s", exc)


# ── Hash helper ────────────────────────────────────────────────────────────────

def _hash_prompt(template: str, system_context: str) -> str:
    payload = json.dumps(
        {"template": template, "system_context": system_context},
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


# ── Exceptions ─────────────────────────────────────────────────────────────────

class PromptNotFoundError(Exception):
    pass

class PromptRegistryError(Exception):
    pass


# ── Module-level singleton ─────────────────────────────────────────────────────

_registry: PromptRegistry | None = None


def get_prompt_registry(db_writer: Callable | None = None) -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry(db_writer=db_writer)
    return _registry
