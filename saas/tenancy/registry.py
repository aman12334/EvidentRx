"""
Tenant and organization registry.

Central in-memory + DB-backed registry for all tenants and organizations.
The registry is the single source of truth for tenant existence checks,
feature flag lookups, and organization hierarchy resolution.

All mutations go through the registry so that downstream components
(isolation guard, billing meter, notification dispatcher) stay consistent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from saas.tenancy.models import (
    Organization,
    OrgType,
    Tenant,
    TenantContact,
    TenantFeatureFlags,
    TenantStatus,
    TenantTier,
    new_org_id,
    new_tenant_id,
)

log = logging.getLogger("evidentrx.saas.tenancy.registry")


class TenantRegistry:
    """
    Registry of all tenants on the platform.

    Provides:
    - Tenant creation / lookup / status transitions
    - Feature flag management
    - Parent/subsidiary resolution
    - Slug → tenant_id resolution (used by API routing)
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._tenants:    dict[str, Tenant] = {}
        self._by_slug:    dict[str, str]    = {}   # slug → tenant_id
        self._flags:      dict[str, TenantFeatureFlags] = {}
        self._db_writer   = db_writer

    # ── Create ─────────────────────────────────────────────────────────────────

    async def create(
        self,
        name:             str,
        slug:             str,
        tier:             TenantTier,
        primary_contact:  TenantContact,
        region:           str           = "us-east-1",
        parent_tenant_id: str | None = None,
        trial_days:       int           = 0,
    ) -> Tenant:
        if slug in self._by_slug:
            raise TenantConflictError(f"Slug '{slug}' is already taken")

        now = datetime.now(tz=UTC)
        tid = new_tenant_id()

        tenant = Tenant(
            tenant_id       = tid,
            name            = name,
            slug            = slug,
            tier            = tier,
            status          = TenantStatus.TRIAL if trial_days > 0 else TenantStatus.PROVISIONING,
            primary_contact = primary_contact,
            region          = region,
            created_at      = now,
            parent_tenant_id= parent_tenant_id,
            trial_ends_at   = (
                datetime(now.year, now.month, now.day + trial_days, tzinfo=UTC)
                if trial_days > 0 else None
            ),
        )
        self._tenants[tid]   = tenant
        self._by_slug[slug]  = tid
        self._flags[tid]     = _default_flags(tid, tier)
        await self._persist("create_tenant", tenant)
        log.info("TenantRegistry: created tenant '%s' [%s] tier=%s", name, tid[:8], tier.value)
        return tenant

    # ── Status transitions ─────────────────────────────────────────────────────

    async def activate(self, tenant_id: str) -> Tenant:
        t = self._get(tenant_id)
        t.status = TenantStatus.ACTIVE
        await self._persist("update_tenant", t)
        log.info("TenantRegistry: activated tenant %s", tenant_id[:8])
        return t

    async def suspend(self, tenant_id: str, reason: str) -> Tenant:
        t = self._get(tenant_id)
        t.status       = TenantStatus.SUSPENDED
        t.suspended_at = datetime.now(tz=UTC)
        t.metadata["suspension_reason"] = reason
        await self._persist("update_tenant", t)
        log.warning("TenantRegistry: suspended tenant %s — %s", tenant_id[:8], reason)
        return t

    async def archive(self, tenant_id: str) -> Tenant:
        t = self._get(tenant_id)
        t.status      = TenantStatus.ARCHIVED
        t.archived_at = datetime.now(tz=UTC)
        await self._persist("update_tenant", t)
        log.info("TenantRegistry: archived tenant %s", tenant_id[:8])
        return t

    # ── Feature flags ──────────────────────────────────────────────────────────

    def get_flags(self, tenant_id: str) -> TenantFeatureFlags:
        return self._flags.get(tenant_id) or _default_flags(tenant_id, TenantTier.STARTER)

    async def set_flag(self, tenant_id: str, flag: str, value: Any) -> None:
        flags = self._flags.setdefault(tenant_id, _default_flags(tenant_id, TenantTier.STARTER))
        if hasattr(flags, flag):
            setattr(flags, flag, value)
        else:
            flags.custom[flag] = value
        await self._persist("update_flags", flags)

    # ── Queries ────────────────────────────────────────────────────────────────

    def get(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def get_by_slug(self, slug: str) -> Tenant | None:
        tid = self._by_slug.get(slug)
        return self._tenants.get(tid) if tid else None

    def list_active(self, region: str | None = None) -> list[Tenant]:
        return [
            t for t in self._tenants.values()
            if t.status == TenantStatus.ACTIVE
            and (region is None or t.region == region)
        ]

    def list_subsidiaries(self, parent_tenant_id: str) -> list[Tenant]:
        return [
            t for t in self._tenants.values()
            if t.parent_tenant_id == parent_tenant_id
        ]

    def count(self) -> int:
        return len(self._tenants)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get(self, tenant_id: str) -> Tenant:
        t = self._tenants.get(tenant_id)
        if t is None:
            raise TenantNotFoundError(tenant_id)
        return t

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("TenantRegistry: persist failed: %s", exc)


class OrganizationRegistry:
    """
    Registry of organizations within tenants.

    Organizations form a tree rooted at the tenant level. An org may
    have a parent org (subsidiary → parent) and holds references to
    covered entities. All orgs are strictly tenant-scoped.
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._orgs:       dict[str, Organization] = {}
        self._by_tenant:  dict[str, list[str]] = {}   # tenant_id → [org_id]
        self._db_writer   = db_writer

    async def create(
        self,
        tenant_id:     str,
        name:          str,
        org_type:      OrgType,
        region:        str           = "us",
        parent_org_id: str | None = None,
        metadata:      dict | None= None,
    ) -> Organization:
        org = Organization(
            org_id        = new_org_id(),
            tenant_id     = tenant_id,
            name          = name,
            org_type      = org_type,
            parent_org_id = parent_org_id,
            region        = region,
            metadata      = metadata or {},
        )
        self._orgs[org.org_id] = org
        self._by_tenant.setdefault(tenant_id, []).append(org.org_id)
        await self._persist("create_org", org)
        return org

    async def add_covered_entity(self, org_id: str, entity_id: str) -> Organization:
        org = self._require(org_id)
        if entity_id not in org.covered_entity_ids:
            org.covered_entity_ids.append(entity_id)
        await self._persist("update_org", org)
        return org

    def get(self, org_id: str) -> Organization | None:
        return self._orgs.get(org_id)

    def list_for_tenant(
        self,
        tenant_id: str,
        org_type:  OrgType | None = None,
    ) -> list[Organization]:
        ids  = self._by_tenant.get(tenant_id, [])
        orgs = [self._orgs[i] for i in ids if i in self._orgs and self._orgs[i].active]
        if org_type:
            orgs = [o for o in orgs if o.org_type == org_type]
        return sorted(orgs, key=lambda o: o.name)

    def children(self, org_id: str) -> list[Organization]:
        org = self._require(org_id)
        return [
            o for o in self._orgs.values()
            if o.parent_org_id == org_id and o.tenant_id == org.tenant_id
        ]

    def ancestors(self, org_id: str) -> list[Organization]:
        """Return the ancestor chain from root to direct parent (root first)."""
        chain: list[Organization] = []
        current = self._orgs.get(org_id)
        visited: set[str] = set()
        while current and current.parent_org_id and current.parent_org_id not in visited:
            parent = self._orgs.get(current.parent_org_id)
            if parent:
                chain.append(parent)
                visited.add(current.parent_org_id)
            current = parent
        chain.reverse()
        return chain

    def _require(self, org_id: str) -> Organization:
        org = self._orgs.get(org_id)
        if org is None:
            raise OrgNotFoundError(org_id)
        return org

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("OrganizationRegistry: persist failed: %s", exc)


# ── Feature flag defaults by tier ──────────────────────────────────────────────

def _default_flags(tenant_id: str, tier: TenantTier) -> TenantFeatureFlags:
    flags = TenantFeatureFlags(tenant_id=tenant_id)
    if tier == TenantTier.ENTERPRISE:
        flags.enable_learning_layer    = True
        flags.enable_experimentation   = True
        flags.enable_marketplace       = True
        flags.enable_bulk_export       = True
        flags.max_concurrent_investigations = 500
        flags.max_api_keys             = 50
        flags.max_users                = 10_000
    elif tier == TenantTier.PROFESSIONAL:
        flags.enable_learning_layer    = False
        flags.max_concurrent_investigations = 150
        flags.max_api_keys             = 20
        flags.max_users                = 500
    elif tier == TenantTier.GOVERNMENT:
        flags.enable_learning_layer    = True
        flags.enable_bulk_export       = True
        flags.max_concurrent_investigations = 1000
        flags.max_api_keys             = 100
        flags.max_users                = 50_000
    return flags


# ── Exceptions ─────────────────────────────────────────────────────────────────

class TenantNotFoundError(Exception):
    pass

class TenantConflictError(Exception):
    pass

class OrgNotFoundError(Exception):
    pass


# ── Singletons ─────────────────────────────────────────────────────────────────

_tenant_registry: TenantRegistry | None = None
_org_registry:    OrganizationRegistry | None = None


def get_tenant_registry(db_writer: Callable | None = None) -> TenantRegistry:
    global _tenant_registry
    if _tenant_registry is None:
        _tenant_registry = TenantRegistry(db_writer=db_writer)
    return _tenant_registry


def get_org_registry(db_writer: Callable | None = None) -> OrganizationRegistry:
    global _org_registry
    if _org_registry is None:
        _org_registry = OrganizationRegistry(db_writer=db_writer)
    return _org_registry
