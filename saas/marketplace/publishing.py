"""
Template publishing workflow.

Governs the lifecycle of a template from initial submission through
platform review to marketplace publication. The approval gate ensures
no template reaches tenants without explicit platform-admin sign-off.

Publishing pipeline
───────────────────
  1. Author submits a template (DRAFT)
  2. Author requests review → REVIEW
  3. Platform reviewer approves or rejects
     ├── Approved → PUBLISHED  (registered in MarketplaceRegistry)
     └── Rejected → back to DRAFT with rejection notes
  4. Publisher may DEPRECATE or WITHDRAW a published template
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Callable, Optional

from saas.marketplace.templates import (
    WorkflowTemplate,
    TemplateType,
    TemplateVisibility,
    MarketplaceStatus,
)
from saas.marketplace.registry import MarketplaceRegistry, get_marketplace_registry

log = logging.getLogger("evidentrx.saas.marketplace.publishing")


@dataclass
class PublishingRequest:
    """
    Tracks one publishing lifecycle for a WorkflowTemplate.

    A new PublishingRequest is created each time a template is submitted
    for review — including re-submissions after rejection.
    """
    request_id:    str
    template_id:   str
    submitted_by:  str
    tenant_id:     str
    submitted_at:  datetime
    status:        str                    # "pending" | "approved" | "rejected"
    reviewer_id:   Optional[str]          = None
    reviewed_at:   Optional[datetime]     = None
    review_notes:  str                    = ""
    content_hash:  str                    = ""    # snapshot of template at submission

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id":   self.request_id,
            "template_id":  self.template_id,
            "submitted_by": self.submitted_by,
            "tenant_id":    self.tenant_id,
            "submitted_at": self.submitted_at.isoformat(),
            "status":       self.status,
            "reviewer_id":  self.reviewer_id,
            "reviewed_at":  self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_notes": self.review_notes,
        }


def _hash_definition(workflow_definition: dict[str, Any]) -> str:
    raw = json.dumps(workflow_definition, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


class TemplatePublisher:
    """
    Manages template submission, review, approval, and publication.

    Platform reviewers (PLATFORM_ADMIN role) approve or reject pending
    requests. The publisher tenant can only see their own templates.
    """

    def __init__(
        self,
        registry:  Optional[MarketplaceRegistry] = None,
        db_writer: Optional[Callable]            = None,
    ) -> None:
        self._registry  = registry or get_marketplace_registry()
        self._db_writer = db_writer
        # template_id → WorkflowTemplate
        self._templates: dict[str, WorkflowTemplate] = {}
        # request_id → PublishingRequest
        self._requests: dict[str, PublishingRequest] = {}
        # template_id → [request_id, ...]  (history)
        self._by_template: dict[str, list[str]] = {}

    # ── Authoring ──────────────────────────────────────────────────────────────

    def create_template(
        self,
        tenant_id:           str,
        created_by:          str,
        name:                str,
        version:             str,
        template_type:       TemplateType,
        title:               str,
        description:         str,
        workflow_definition: dict[str, Any],
        visibility:          TemplateVisibility      = TemplateVisibility.PUBLIC,
        tags:                Optional[list[str]]     = None,
        compatible_tiers:    Optional[list[str]]     = None,
        allowed_tenant_ids:  Optional[list[str]]     = None,
        parent_template_id:  Optional[str]           = None,
        metadata:            Optional[dict[str, Any]] = None,
    ) -> WorkflowTemplate:
        """Create a new DRAFT template in the publisher's workspace."""
        template_id   = str(uuid.uuid4())
        content_hash  = _hash_definition(workflow_definition)

        tmpl = WorkflowTemplate(
            template_id          = template_id,
            name                 = name,
            version              = version,
            template_type        = template_type,
            title                = title,
            description          = description,
            workflow_definition  = workflow_definition,
            status               = MarketplaceStatus.DRAFT,
            visibility           = visibility,
            content_hash         = content_hash,
            publisher_tenant_id  = tenant_id,
            created_at           = datetime.now(tz=timezone.utc),
            created_by           = created_by,
            tags                 = tags or [],
            compatible_tiers     = compatible_tiers or [],
            allowed_tenant_ids   = allowed_tenant_ids or [],
            parent_template_id   = parent_template_id,
            metadata             = metadata or {},
        )
        self._templates[template_id] = tmpl
        self._by_template[template_id] = []
        log.info(
            "TemplatePublisher: created draft template '%s' v%s by tenant %s",
            name, version, tenant_id[:8],
        )
        return tmpl

    def update_draft(
        self,
        template_id:         str,
        tenant_id:           str,
        workflow_definition: Optional[dict[str, Any]] = None,
        title:               Optional[str]            = None,
        description:         Optional[str]            = None,
        tags:                Optional[list[str]]      = None,
    ) -> WorkflowTemplate:
        tmpl = self._get_owned_template(template_id, tenant_id)
        if tmpl.status != MarketplaceStatus.DRAFT:
            raise PublishingError(
                f"Template {template_id[:8]} is not in DRAFT status — cannot edit"
            )
        if workflow_definition is not None:
            tmpl.workflow_definition = workflow_definition
            tmpl.content_hash        = _hash_definition(workflow_definition)
        if title is not None:
            tmpl.title = title
        if description is not None:
            tmpl.description = description
        if tags is not None:
            tmpl.tags = tags
        return tmpl

    # ── Submission & review ────────────────────────────────────────────────────

    def submit_for_review(
        self,
        template_id:  str,
        tenant_id:    str,
        submitted_by: str,
    ) -> PublishingRequest:
        tmpl = self._get_owned_template(template_id, tenant_id)
        if tmpl.status not in (MarketplaceStatus.DRAFT,):
            raise PublishingError(
                f"Template {template_id[:8]} must be DRAFT to submit for review"
            )
        tmpl.status = MarketplaceStatus.REVIEW

        req = PublishingRequest(
            request_id   = str(uuid.uuid4()),
            template_id  = template_id,
            submitted_by = submitted_by,
            tenant_id    = tenant_id,
            submitted_at = datetime.now(tz=timezone.utc),
            status       = "pending",
            content_hash = tmpl.content_hash,
        )
        self._requests[req.request_id] = req
        self._by_template[template_id].append(req.request_id)
        log.info(
            "TemplatePublisher: template '%s' submitted for review (request %s)",
            tmpl.name, req.request_id[:8],
        )
        return req

    def approve(
        self,
        request_id:    str,
        reviewer_id:   str,
        review_notes:  str = "",
        change_summary: str = "",
    ) -> WorkflowTemplate:
        """
        Approve a pending request.

        Transitions the template to PUBLISHED and registers it in the
        MarketplaceRegistry, then creates upgrade notifications for any
        tenants running an older version.
        """
        req = self._get_pending_request(request_id)
        tmpl = self._templates.get(req.template_id)
        if tmpl is None:
            raise PublishingError(f"Template {req.template_id} not found")
        if tmpl.content_hash != req.content_hash:
            raise PublishingError(
                "Template definition changed since submission — re-submit required"
            )

        req.status      = "approved"
        req.reviewer_id = reviewer_id
        req.reviewed_at = datetime.now(tz=timezone.utc)
        req.review_notes = review_notes

        tmpl.status       = MarketplaceStatus.PUBLISHED
        tmpl.published_at = req.reviewed_at

        self._registry.register_template(tmpl)
        notifications = self._registry.create_upgrade_notifications(tmpl, change_summary)

        log.info(
            "TemplatePublisher: approved template '%s' v%s — %d upgrade notifications",
            tmpl.name, tmpl.version, len(notifications),
        )
        return tmpl

    def reject(
        self,
        request_id:   str,
        reviewer_id:  str,
        review_notes: str,
    ) -> WorkflowTemplate:
        req = self._get_pending_request(request_id)
        tmpl = self._templates.get(req.template_id)
        if tmpl is None:
            raise PublishingError(f"Template {req.template_id} not found")

        req.status       = "rejected"
        req.reviewer_id  = reviewer_id
        req.reviewed_at  = datetime.now(tz=timezone.utc)
        req.review_notes = review_notes

        tmpl.status = MarketplaceStatus.DRAFT   # back to DRAFT for edits
        log.info(
            "TemplatePublisher: rejected template '%s' — notes: %s",
            tmpl.name, review_notes[:80],
        )
        return tmpl

    # ── Post-publish actions ───────────────────────────────────────────────────

    def deprecate(
        self,
        template_id: str,
        tenant_id:   str,
        reason:      str = "",
    ) -> None:
        tmpl = self._get_owned_template(template_id, tenant_id)
        if tmpl.status != MarketplaceStatus.PUBLISHED:
            raise PublishingError(f"Template {template_id[:8]} is not PUBLISHED")
        self._registry.deprecate_template(template_id, reason)
        tmpl.status = MarketplaceStatus.DEPRECATED

    def withdraw(
        self,
        template_id: str,
        tenant_id:   str,
        reason:      str = "",
    ) -> None:
        """
        Withdraw a template entirely (stronger than deprecation).
        Withdrawn templates are invisible to all tenants.
        """
        tmpl = self._get_owned_template(template_id, tenant_id)
        tmpl.status = MarketplaceStatus.WITHDRAWN
        tmpl.metadata["withdrawal_reason"] = reason
        log.info("TemplatePublisher: withdrew template %s", template_id[:8])

    # ── Queries ────────────────────────────────────────────────────────────────

    def list_pending_reviews(self) -> list[PublishingRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]

    def list_tenant_templates(
        self,
        tenant_id: str,
        status:    Optional[MarketplaceStatus] = None,
    ) -> list[WorkflowTemplate]:
        return [
            t for t in self._templates.values()
            if t.publisher_tenant_id == tenant_id
            and (status is None or t.status == status)
        ]

    def get_request(self, request_id: str) -> Optional[PublishingRequest]:
        return self._requests.get(request_id)

    def request_history(self, template_id: str) -> list[PublishingRequest]:
        ids = self._by_template.get(template_id, [])
        return [self._requests[rid] for rid in ids if rid in self._requests]

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_owned_template(
        self,
        template_id: str,
        tenant_id:   str,
    ) -> WorkflowTemplate:
        tmpl = self._templates.get(template_id)
        if tmpl is None or tmpl.publisher_tenant_id != tenant_id:
            raise TemplateOwnershipError(template_id, tenant_id)
        return tmpl

    def _get_pending_request(self, request_id: str) -> PublishingRequest:
        req = self._requests.get(request_id)
        if req is None:
            raise PublishingError(f"PublishingRequest {request_id} not found")
        if req.status != "pending":
            raise PublishingError(
                f"Request {request_id[:8]} is already {req.status}"
            )
        return req


# ── Exceptions ─────────────────────────────────────────────────────────────────

class PublishingError(Exception):
    pass


class TemplateOwnershipError(Exception):
    def __init__(self, template_id: str, tenant_id: str) -> None:
        super().__init__(
            f"Tenant {tenant_id[:8]} does not own template {template_id[:8]}"
        )


# ── Singleton ──────────────────────────────────────────────────────────────────

_publisher: Optional[TemplatePublisher] = None


def get_template_publisher(
    registry:  Optional[MarketplaceRegistry] = None,
    db_writer: Optional[Callable]            = None,
) -> TemplatePublisher:
    global _publisher
    if _publisher is None:
        _publisher = TemplatePublisher(registry=registry, db_writer=db_writer)
    return _publisher
