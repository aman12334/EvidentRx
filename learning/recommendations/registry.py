"""
Recommendation template versioning registry.

Maintains immutable, versioned recommendation templates. A template
defines the pattern for generating recommendations of a given type.
Templates are promoted, deprecated, or rolled back through a governed
approval workflow — no template change takes effect unilaterally.

Template lifecycle
──────────────────
  DRAFT → REVIEW → ACTIVE ← ROLLBACK_TARGET
                ↘ REJECTED
  ACTIVE → DEPRECATED (when superseded)
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

from learning.recommendations.tracker import RecommendationType

log = logging.getLogger("evidentrx.learning.recommendations.registry")


class TemplateStatus(str, Enum):
    DRAFT      = "draft"
    REVIEW     = "review"
    ACTIVE     = "active"
    DEPRECATED = "deprecated"
    REJECTED   = "rejected"


@dataclass
class RecommendationTemplate:
    """
    Versioned recommendation template.

    The template defines the prompt/pattern used to generate recommendations
    of a specific type. It carries a content_hash for tamper detection
    and a parent_version for rollback chain tracking.
    """
    template_id:     str
    tenant_id:       str
    rec_type:        RecommendationType
    version:         str                    # semantic: "major.minor.patch"
    title:           str
    content_pattern: str                    # Jinja2-compatible template string
    guidance:        str                    # Human-readable usage guidance
    status:          TemplateStatus
    content_hash:    str                    # SHA-256 of content_pattern
    created_at:      datetime
    created_by:      str
    approved_by:     Optional[str]          = None
    approved_at:     Optional[datetime]     = None
    parent_version:  Optional[str]          = None
    effectiveness_threshold: float          = 0.50   # minimum score before flagging
    metadata:        dict[str, Any]         = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id":    self.template_id,
            "tenant_id":      self.tenant_id,
            "rec_type":       self.rec_type.value,
            "version":        self.version,
            "title":          self.title,
            "status":         self.status.value,
            "content_hash":   self.content_hash,
            "created_at":     self.created_at.isoformat(),
            "created_by":     self.created_by,
            "approved_by":    self.approved_by,
            "parent_version": self.parent_version,
        }


class RecommendationTemplateRegistry:
    """
    Governed registry of versioned recommendation templates.

    Provides CRUD with approval workflow and rollback support.
    """

    def __init__(self, db_writer: Optional[Callable] = None) -> None:
        self._templates:     dict[str, RecommendationTemplate] = {}
        self._active_by_type: dict[tuple[str, str], str] = {}
        # (tenant_id, rec_type.value) → template_id
        self._db_writer      = db_writer

    # ── Create ─────────────────────────────────────────────────────────────────

    async def register(
        self,
        tenant_id:       str,
        rec_type:        RecommendationType,
        version:         str,
        title:           str,
        content_pattern: str,
        guidance:        str,
        created_by:      str,
        parent_version:  Optional[str]  = None,
        metadata:        Optional[dict] = None,
    ) -> RecommendationTemplate:
        """Register a new template in DRAFT status."""
        content_hash = hashlib.sha256(content_pattern.encode()).hexdigest()

        template = RecommendationTemplate(
            template_id      = str(uuid.uuid4()),
            tenant_id        = tenant_id,
            rec_type         = rec_type,
            version          = version,
            title            = title,
            content_pattern  = content_pattern,
            guidance         = guidance,
            status           = TemplateStatus.DRAFT,
            content_hash     = content_hash,
            created_at       = datetime.now(tz=timezone.utc),
            created_by       = created_by,
            parent_version   = parent_version,
            metadata         = metadata or {},
        )

        self._templates[template.template_id] = template
        await self._persist("create", template)
        log.info(
            "TemplateRegistry: registered %s v%s [%s] by %s",
            rec_type.value, version, template.template_id[:8], created_by,
        )
        return template

    # ── Transitions ────────────────────────────────────────────────────────────

    async def submit_for_review(self, template_id: str) -> RecommendationTemplate:
        t = self._require(template_id, TemplateStatus.DRAFT)
        t.status = TemplateStatus.REVIEW
        await self._persist("update", t)
        return t

    async def approve(self, template_id: str, approved_by: str) -> RecommendationTemplate:
        """Approve a template and set it as ACTIVE for its type."""
        t = self._require(template_id, TemplateStatus.REVIEW)
        t.status      = TemplateStatus.ACTIVE
        t.approved_by = approved_by
        t.approved_at = datetime.now(tz=timezone.utc)

        # Deprecate current active template for this type
        key = (t.tenant_id, t.rec_type.value)
        current_id = self._active_by_type.get(key)
        if current_id and current_id != template_id:
            current = self._templates.get(current_id)
            if current:
                current.status = TemplateStatus.DEPRECATED
                await self._persist("update", current)

        self._active_by_type[key] = template_id
        await self._persist("update", t)
        log.info(
            "TemplateRegistry: %s v%s APPROVED by %s",
            t.rec_type.value, t.version, approved_by,
        )
        return t

    async def reject(self, template_id: str, rejected_by: str, reason: str) -> RecommendationTemplate:
        t = self._require(template_id, TemplateStatus.REVIEW)
        t.status   = TemplateStatus.REJECTED
        t.metadata["rejection_reason"] = reason
        t.metadata["rejected_by"]      = rejected_by
        await self._persist("update", t)
        return t

    async def rollback(self, tenant_id: str, rec_type: RecommendationType, target_version: str, rolled_by: str) -> RecommendationTemplate:
        """Roll back to a prior ACTIVE or DEPRECATED template version."""
        target = next(
            (t for t in self._templates.values()
             if t.tenant_id == tenant_id
             and t.rec_type == rec_type
             and t.version == target_version
             and t.status in (TemplateStatus.ACTIVE, TemplateStatus.DEPRECATED)),
            None,
        )
        if target is None:
            raise TemplateNotFoundError(f"No template {rec_type.value} v{target_version} for tenant {tenant_id}")

        # Re-approve the target
        target.status      = TemplateStatus.REVIEW
        target.approved_by = None
        return await self.approve(target.template_id, approved_by=rolled_by)

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_active(
        self,
        tenant_id: str,
        rec_type:  RecommendationType,
    ) -> Optional[RecommendationTemplate]:
        key = (tenant_id, rec_type.value)
        tid = self._active_by_type.get(key)
        return self._templates.get(tid) if tid else None

    def list_for_tenant(
        self,
        tenant_id: str,
        rec_type:  Optional[RecommendationType] = None,
    ) -> list[RecommendationTemplate]:
        result = [
            t for t in self._templates.values()
            if t.tenant_id == tenant_id
            and (rec_type is None or t.rec_type == rec_type)
        ]
        return sorted(result, key=lambda t: t.created_at, reverse=True)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _require(self, template_id: str, expected: TemplateStatus) -> RecommendationTemplate:
        t = self._templates.get(template_id)
        if t is None:
            raise TemplateNotFoundError(template_id)
        if t.status != expected:
            raise TemplateTransitionError(
                f"Template {template_id[:8]} is {t.status.value}, expected {expected.value}"
            )
        return t

    async def _persist(self, op: str, template: RecommendationTemplate) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, template)
            except Exception as exc:
                log.error("TemplateRegistry: persist failed: %s", exc)


class TemplateNotFoundError(Exception):
    pass

class TemplateTransitionError(Exception):
    pass
