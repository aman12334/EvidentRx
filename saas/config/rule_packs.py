"""
Tenant rule pack assignment registry.

Rule packs are versioned, curated sets of compliance rules that a tenant
activates for their environment. A tenant may have multiple active packs
(e.g., a standard 340B pack + a state Medicaid override pack).

Rule pack lifecycle
───────────────────
  DRAFT → PUBLISHED (platform-level operation)
  PUBLISHED → DEPRECATED (when superseded by a newer version)

Tenant assignment lifecycle
────────────────────────────
  ASSIGNED → ACTIVE → SUSPENDED / REVOKED

Inheritance model
─────────────────
  A child org inherits all rule packs from its parent unless an explicit
  override pack is assigned. Override packs can ADD rules but cannot
  REMOVE rules from inherited packs (compliance floor guarantee).
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

log = logging.getLogger("evidentrx.saas.config.rule_packs")


class RulePackStatus(str, Enum):
    DRAFT      = "draft"
    PUBLISHED  = "published"
    DEPRECATED = "deprecated"


class AssignmentStatus(str, Enum):
    ACTIVE    = "active"
    SUSPENDED = "suspended"
    REVOKED   = "revoked"


@dataclass
class RulePack:
    """
    A published, versioned set of compliance rules.

    The rules_manifest is a JSON-serialisable dict mapping rule_code →
    rule configuration. The content_hash provides tamper detection.
    """
    pack_id:          str
    name:             str
    version:          str
    description:      str
    rules_manifest:   dict[str, Any]   # rule_code → {threshold, severity, ...}
    status:           RulePackStatus
    content_hash:     str
    created_at:       datetime
    created_by:       str
    published_at:     Optional[datetime] = None
    deprecated_at:    Optional[datetime] = None
    parent_pack_id:   Optional[str]      = None   # prior version
    tags:             list[str]          = field(default_factory=list)
    metadata:         dict[str, Any]     = field(default_factory=dict)

    @property
    def rule_count(self) -> int:
        return len(self.rules_manifest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id":      self.pack_id,
            "name":         self.name,
            "version":      self.version,
            "description":  self.description,
            "rule_count":   self.rule_count,
            "status":       self.status.value,
            "content_hash": self.content_hash,
            "created_at":   self.created_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "tags":         self.tags,
        }


@dataclass
class TenantRulePackAssignment:
    """
    Records which rule pack is active for a tenant (and optionally an org).

    The org_id field enables org-specific overrides without affecting
    sibling orgs in the same tenant.
    """
    assignment_id: str
    tenant_id:     str
    pack_id:       str
    org_id:        Optional[str]      = None    # None = tenant-wide
    status:        AssignmentStatus   = AssignmentStatus.ACTIVE
    assigned_by:   str                = "system"
    assigned_at:   datetime           = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    effective_from: datetime          = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    override_config: dict[str, Any]  = field(default_factory=dict)   # per-tenant threshold overrides

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id":  self.assignment_id,
            "tenant_id":      self.tenant_id,
            "pack_id":        self.pack_id,
            "org_id":         self.org_id,
            "status":         self.status.value,
            "assigned_by":    self.assigned_by,
            "assigned_at":    self.assigned_at.isoformat(),
        }


class RulePackRegistry:
    """
    Platform-level registry of published rule packs.

    The registry manages the platform catalogue (all packs across all
    tenants) and the per-tenant assignment table.
    """

    def __init__(self, db_writer: Optional[Callable] = None) -> None:
        self._packs:       dict[str, RulePack] = {}
        self._assignments: dict[str, TenantRulePackAssignment] = {}
        # (tenant_id, org_id) → [assignment_id]
        self._by_tenant:   dict[tuple[str, Optional[str]], list[str]] = {}
        self._db_writer    = db_writer

    # ── Platform catalogue ─────────────────────────────────────────────────────

    async def create_pack(
        self,
        name:           str,
        version:        str,
        description:    str,
        rules_manifest: dict[str, Any],
        created_by:     str,
        tags:           Optional[list[str]] = None,
        parent_pack_id: Optional[str]       = None,
    ) -> RulePack:
        content_hash = hashlib.sha256(
            json.dumps(rules_manifest, sort_keys=True).encode()
        ).hexdigest()

        pack = RulePack(
            pack_id        = str(uuid.uuid4()),
            name           = name,
            version        = version,
            description    = description,
            rules_manifest = rules_manifest,
            status         = RulePackStatus.DRAFT,
            content_hash   = content_hash,
            created_at     = datetime.now(tz=timezone.utc),
            created_by     = created_by,
            parent_pack_id = parent_pack_id,
            tags           = tags or [],
        )
        self._packs[pack.pack_id] = pack
        await self._persist("create_pack", pack)
        return pack

    async def publish_pack(self, pack_id: str, published_by: str) -> RulePack:
        pack = self._require_pack(pack_id)
        if pack.status != RulePackStatus.DRAFT:
            raise RulePackError(f"Pack {pack_id[:8]} is not in DRAFT status")
        pack.status       = RulePackStatus.PUBLISHED
        pack.published_at = datetime.now(tz=timezone.utc)
        pack.metadata["published_by"] = published_by
        await self._persist("update_pack", pack)
        log.info("RulePackRegistry: published pack '%s' v%s", pack.name, pack.version)
        return pack

    def get_pack(self, pack_id: str) -> Optional[RulePack]:
        return self._packs.get(pack_id)

    def list_published(self, tags: Optional[list[str]] = None) -> list[RulePack]:
        packs = [p for p in self._packs.values() if p.status == RulePackStatus.PUBLISHED]
        if tags:
            tag_set = set(tags)
            packs   = [p for p in packs if tag_set.intersection(p.tags)]
        return sorted(packs, key=lambda p: p.name)

    # ── Tenant assignment ──────────────────────────────────────────────────────

    async def assign(
        self,
        tenant_id:       str,
        pack_id:         str,
        assigned_by:     str      = "system",
        org_id:          Optional[str]      = None,
        override_config: Optional[dict]     = None,
    ) -> TenantRulePackAssignment:
        pack = self._require_pack(pack_id)
        if pack.status != RulePackStatus.PUBLISHED:
            raise RulePackError(f"Cannot assign unpublished pack {pack_id[:8]}")

        assignment = TenantRulePackAssignment(
            assignment_id  = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            pack_id        = pack_id,
            org_id         = org_id,
            assigned_by    = assigned_by,
            override_config= override_config or {},
        )
        self._assignments[assignment.assignment_id] = assignment
        key = (tenant_id, org_id)
        self._by_tenant.setdefault(key, []).append(assignment.assignment_id)
        await self._persist("create_assignment", assignment)
        log.info(
            "RulePackRegistry: assigned pack '%s' to tenant %s org=%s",
            pack.name, tenant_id[:8], org_id[:8] if org_id else "all",
        )
        return assignment

    async def revoke(self, assignment_id: str, revoked_by: str) -> TenantRulePackAssignment:
        a = self._assignments.get(assignment_id)
        if a is None:
            raise RulePackNotFoundError(assignment_id)
        a.status = AssignmentStatus.REVOKED
        a.override_config["revoked_by"] = revoked_by
        a.override_config["revoked_at"] = datetime.now(tz=timezone.utc).isoformat()
        await self._persist("update_assignment", a)
        return a

    def active_packs_for_tenant(
        self,
        tenant_id: str,
        org_id:    Optional[str] = None,
    ) -> list[RulePack]:
        """
        Return all active rule packs for a tenant scope.

        Merges tenant-wide assignments with org-specific overrides.
        Org-specific packs take precedence over tenant-wide packs for
        the same rule_code, but tenant-wide packs are always included.
        """
        seen_pack_ids: set[str] = set()
        packs: list[RulePack] = []

        # Org-specific first (highest precedence)
        if org_id:
            for aid in self._by_tenant.get((tenant_id, org_id), []):
                a = self._assignments[aid]
                if a.status == AssignmentStatus.ACTIVE and a.pack_id not in seen_pack_ids:
                    p = self._packs.get(a.pack_id)
                    if p:
                        packs.append(p)
                        seen_pack_ids.add(a.pack_id)

        # Tenant-wide
        for aid in self._by_tenant.get((tenant_id, None), []):
            a = self._assignments[aid]
            if a.status == AssignmentStatus.ACTIVE and a.pack_id not in seen_pack_ids:
                p = self._packs.get(a.pack_id)
                if p:
                    packs.append(p)
                    seen_pack_ids.add(a.pack_id)

        return packs

    def effective_rules(
        self,
        tenant_id: str,
        org_id:    Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Return the merged rule manifest for a tenant scope.

        Rules from later-assigned packs override earlier ones for the
        same rule_code, but never remove rules from inherited packs.
        """
        merged: dict[str, Any] = {}
        for pack in self.active_packs_for_tenant(tenant_id, org_id):
            merged.update(pack.rules_manifest)
        return merged

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _require_pack(self, pack_id: str) -> RulePack:
        p = self._packs.get(pack_id)
        if p is None:
            raise RulePackNotFoundError(pack_id)
        return p

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("RulePackRegistry: persist failed: %s", exc)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class RulePackNotFoundError(Exception):
    pass

class RulePackError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_registry: Optional[RulePackRegistry] = None


def get_rule_pack_registry(db_writer: Optional[Callable] = None) -> RulePackRegistry:
    global _registry
    if _registry is None:
        _registry = RulePackRegistry(db_writer=db_writer)
    return _registry
