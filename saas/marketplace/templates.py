"""
Workflow template marketplace.

Provides reusable, versioned workflow templates that tenants can adopt
into their investigation libraries. Templates are curated platform assets
— tenant teams can instantiate and customise them but cannot modify the
source template without going through the publishing workflow.

Template types
──────────────
  INVESTIGATION_PLAYBOOK  — end-to-end investigation workflow
  ESCALATION_WORKFLOW     — escalation routing and review steps
  REMEDIATION_WORKFLOW    — post-investigation remediation steps
  MONITORING_WORKFLOW     — continuous compliance monitoring
  ONBOARDING_PLAYBOOK     — tenant onboarding automation
  AUDIT_WORKFLOW          — regulatory audit response workflow
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

log = logging.getLogger("evidentrx.saas.marketplace.templates")


class TemplateType(str, Enum):
    INVESTIGATION_PLAYBOOK = "investigation_playbook"
    ESCALATION_WORKFLOW    = "escalation_workflow"
    REMEDIATION_WORKFLOW   = "remediation_workflow"
    MONITORING_WORKFLOW    = "monitoring_workflow"
    ONBOARDING_PLAYBOOK    = "onboarding_playbook"
    AUDIT_WORKFLOW         = "audit_workflow"


class TemplateVisibility(str, Enum):
    PUBLIC    = "public"     # available to all tenants
    PRIVATE   = "private"    # creator tenant only
    PARTNER   = "partner"    # specific tenants via allowlist


class MarketplaceStatus(str, Enum):
    DRAFT      = "draft"
    REVIEW     = "review"
    PUBLISHED  = "published"
    DEPRECATED = "deprecated"
    WITHDRAWN  = "withdrawn"


@dataclass
class WorkflowTemplate:
    """
    A versioned, publishable workflow template in the marketplace.

    The workflow_definition is a JSON-serialisable dict following the
    same WorkflowStep schema used in Phase 11's WorkflowRegistry. A
    template adds marketplace metadata (visibility, tags, rating) on top.
    """
    template_id:         str
    name:                str
    version:             str
    template_type:       TemplateType
    title:               str
    description:         str
    workflow_definition: dict[str, Any]   # steps, output_contract, routing
    status:              MarketplaceStatus
    visibility:          TemplateVisibility
    content_hash:        str
    publisher_tenant_id: str
    created_at:          datetime
    created_by:          str
    published_at:        Optional[datetime]  = None
    tags:                list[str]           = field(default_factory=list)
    compatible_tiers:    list[str]           = field(default_factory=list)  # TenantTier values
    install_count:       int                 = 0
    avg_rating:          Optional[float]     = None
    parent_template_id:  Optional[str]       = None
    allowed_tenant_ids:  list[str]           = field(default_factory=list)  # for PARTNER visibility
    metadata:            dict[str, Any]      = field(default_factory=dict)

    @property
    def step_count(self) -> int:
        return len(self.workflow_definition.get("steps", []))

    def is_accessible_to(self, tenant_id: str, tier: str) -> bool:
        if self.status != MarketplaceStatus.PUBLISHED:
            return self.publisher_tenant_id == tenant_id
        if self.visibility == TemplateVisibility.PUBLIC:
            return not self.compatible_tiers or tier in self.compatible_tiers
        if self.visibility == TemplateVisibility.PRIVATE:
            return self.publisher_tenant_id == tenant_id
        if self.visibility == TemplateVisibility.PARTNER:
            return tenant_id in self.allowed_tenant_ids
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id":        self.template_id,
            "name":               self.name,
            "version":            self.version,
            "template_type":      self.template_type.value,
            "title":              self.title,
            "description":        self.description,
            "status":             self.status.value,
            "visibility":         self.visibility.value,
            "content_hash":       self.content_hash,
            "publisher_tenant_id":self.publisher_tenant_id,
            "step_count":         self.step_count,
            "tags":               self.tags,
            "compatible_tiers":   self.compatible_tiers,
            "install_count":      self.install_count,
            "avg_rating":         self.avg_rating,
            "published_at":       self.published_at.isoformat() if self.published_at else None,
        }


@dataclass
class PlaybookEntry:
    """
    A tenant's instantiated copy of a marketplace template.

    When a tenant installs a template, a PlaybookEntry is created in
    their library. They can configure it, but the source template_id
    is tracked for version upgrade notifications.
    """
    entry_id:       str
    tenant_id:      str
    template_id:    str
    template_version: str
    name:           str            # tenant-customised name
    active:         bool           = True
    installed_at:   datetime       = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    installed_by:   str            = "system"
    custom_config:  dict[str, Any] = field(default_factory=dict)
    org_id:         Optional[str]  = None    # scope to specific org if needed

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":         self.entry_id,
            "tenant_id":        self.tenant_id,
            "template_id":      self.template_id,
            "template_version": self.template_version,
            "name":             self.name,
            "active":           self.active,
            "installed_at":     self.installed_at.isoformat(),
        }
