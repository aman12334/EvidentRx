"""
Core tenant and organization models.

Defines the fundamental data structures for the multi-tenant enterprise
SaaS layer. A Tenant is the top-level billing and isolation boundary.
An Organization is a logical grouping within a tenant (e.g. a hospital
system that owns multiple covered entities).

Hierarchy
─────────
  Tenant (billing boundary, API root)
    └── Organization (legal entity / business unit)
          └── CoveredEntity (operational 340B unit, from Phase 1)

Isolation guarantee
───────────────────
  Every runtime object carries a tenant_id. Cross-tenant references are
  structurally impossible — foreign keys are always tenant-scoped and the
  TenantIsolationGuard in isolation.py enforces this at query time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class TenantStatus(str, Enum):
    PROVISIONING = "provisioning"   # being set up
    ACTIVE       = "active"
    SUSPENDED    = "suspended"      # billing / compliance hold
    ARCHIVED     = "archived"       # offboarded, data in cold storage
    TRIAL        = "trial"          # free trial period


class TenantTier(str, Enum):
    STARTER      = "starter"        # single entity, limited volume
    PROFESSIONAL = "professional"   # up to 10 entities
    ENTERPRISE   = "enterprise"     # unlimited entities, full feature set
    GOVERNMENT   = "government"     # FedRAMP / FISMA variant


class OrgType(str, Enum):
    HOSPITAL_SYSTEM    = "hospital_system"
    PHARMACY_NETWORK   = "pharmacy_network"
    COVERED_ENTITY     = "covered_entity"
    CONTRACT_PHARMACY  = "contract_pharmacy"
    REGIONAL_OFFICE    = "regional_office"
    COMPLIANCE_TEAM    = "compliance_team"
    THIRD_PARTY_ADMIN  = "third_party_admin"


@dataclass
class TenantContact:
    """Primary operational contact for a tenant."""
    name:  str
    email: str
    phone: str | None = None
    role:  str           = "admin"


@dataclass
class Tenant:
    """
    Top-level isolation and billing boundary.

    Every API call, database query, event, and audit record is scoped to
    a tenant_id. No cross-tenant data access is permitted at any layer.
    """
    tenant_id:        str
    name:             str
    slug:             str            # URL-safe unique identifier
    tier:             TenantTier
    status:           TenantStatus
    primary_contact:  TenantContact
    region:           str            # "us-east-1" | "us-west-2" | "eu-west-1"
    created_at:       datetime
    trial_ends_at:    datetime | None       = None
    suspended_at:     datetime | None       = None
    archived_at:      datetime | None       = None
    parent_tenant_id: str | None            = None   # for subsidiary structures
    settings:         dict[str, Any]           = field(default_factory=dict)
    feature_flags:    dict[str, bool]          = field(default_factory=dict)
    metadata:         dict[str, Any]           = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE

    @property
    def is_enterprise(self) -> bool:
        return self.tier in (TenantTier.ENTERPRISE, TenantTier.GOVERNMENT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":   self.tenant_id,
            "name":        self.name,
            "slug":        self.slug,
            "tier":        self.tier.value,
            "status":      self.status.value,
            "region":      self.region,
            "created_at":  self.created_at.isoformat(),
            "is_enterprise": self.is_enterprise,
            "parent_tenant_id": self.parent_tenant_id,
        }


@dataclass
class Organization:
    """
    Logical grouping of covered entities within a tenant.

    Supports complex healthcare organization structures: a hospital system
    (org) may own multiple covered entities (operational units). An org
    always belongs to exactly one tenant.
    """
    org_id:           str
    tenant_id:        str
    name:             str
    org_type:         OrgType
    parent_org_id:    str | None        = None    # subsidiary → parent
    covered_entity_ids: list[str]          = field(default_factory=list)
    region:           str                  = "us"
    active:           bool                 = True
    created_at:       datetime             = field(default_factory=lambda: datetime.now(tz=UTC))
    admin_user_ids:   list[str]            = field(default_factory=list)
    settings:         dict[str, Any]       = field(default_factory=dict)
    metadata:         dict[str, Any]       = field(default_factory=dict)

    @property
    def entity_count(self) -> int:
        return len(self.covered_entity_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id":       self.org_id,
            "tenant_id":    self.tenant_id,
            "name":         self.name,
            "org_type":     self.org_type.value,
            "parent_org_id":self.parent_org_id,
            "entity_count": self.entity_count,
            "region":       self.region,
            "active":       self.active,
            "created_at":   self.created_at.isoformat(),
        }


@dataclass
class TenantFeatureFlags:
    """
    Runtime feature flag set for a tenant.

    Used to gate capabilities by tier or during staged rollout. All
    flags default to the safest / most restrictive value.
    """
    tenant_id:                str
    enable_ai_investigation:  bool = True
    enable_graph_intelligence: bool = True
    enable_interoperability:  bool = True
    enable_learning_layer:    bool = False   # Phase 11 — enterprise only
    enable_experimentation:   bool = False
    enable_marketplace:       bool = False
    enable_webhooks:          bool = True
    enable_bulk_export:       bool = False
    max_concurrent_investigations: int = 50
    max_api_keys:             int = 10
    max_users:                int = 100
    custom:                   dict[str, Any] = field(default_factory=dict)

    def is_enabled(self, flag: str) -> bool:
        return getattr(self, flag, self.custom.get(flag, False))


def new_tenant_id() -> str:
    return f"ten_{uuid.uuid4().hex[:16]}"

def new_org_id() -> str:
    return f"org_{uuid.uuid4().hex[:16]}"
