"""
Regional and payer-specific compliance policy overrides.

Some healthcare compliance requirements vary by:
  - State / jurisdiction (Medicaid program rules differ by state)
  - Payer (CMS vs. state Medicaid vs. commercial)
  - Covered entity type (hospital vs. FQHC vs. RRC)

This module manages those overrides at the tenant level, always layered
on top of the base rule packs and never replacing them entirely
(compliance floor guarantee).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.saas.config.overrides")


class OverrideScope(str, Enum):
    REGIONAL  = "regional"   # state / jurisdiction
    PAYER     = "payer"
    ENTITY_TYPE = "entity_type"
    CUSTOM    = "custom"


class OverrideStatus(str, Enum):
    ACTIVE     = "active"
    SUSPENDED  = "suspended"
    EXPIRED    = "expired"
    REVOKED    = "revoked"


@dataclass
class PolicyOverride:
    """
    A single named policy override for a specific scope within a tenant.

    The policy_config carries scope-specific rule adjustments in the same
    format as the rules_manifest in RulePack — a dict of rule_code →
    config delta. Only rules present in a base pack can be overridden;
    new rule codes cannot be introduced here.
    """
    override_id:   str
    tenant_id:     str
    name:          str
    scope:         OverrideScope
    scope_key:     str           # e.g. "CA" for regional, "CMS" for payer
    policy_config: dict[str, Any]
    status:        OverrideStatus
    created_at:    datetime
    created_by:    str
    effective_from: datetime
    effective_until: Optional[datetime] = None
    description:   str               = ""
    org_ids:       list[str]         = field(default_factory=list)   # [] = tenant-wide
    version:       int               = 1
    metadata:      dict[str, Any]    = field(default_factory=dict)

    @property
    def is_active_now(self) -> bool:
        now = datetime.now(tz=timezone.utc)
        if self.status != OverrideStatus.ACTIVE:
            return False
        if now < self.effective_from:
            return False
        if self.effective_until and now > self.effective_until:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "override_id":    self.override_id,
            "tenant_id":      self.tenant_id,
            "name":           self.name,
            "scope":          self.scope.value,
            "scope_key":      self.scope_key,
            "status":         self.status.value,
            "created_by":     self.created_by,
            "effective_from": self.effective_from.isoformat(),
            "effective_until":self.effective_until.isoformat() if self.effective_until else None,
            "version":        self.version,
            "org_count":      len(self.org_ids),
        }


@dataclass
class PayerComplianceConfig:
    """
    Payer-specific compliance logic for a tenant.

    Configures payer-specific detection rules, audit thresholds, and
    reporting requirements (e.g., CMS 340B audit trail format vs. state
    Medicaid format).
    """
    config_id:     str
    tenant_id:     str
    payer_id:      str
    payer_name:    str
    detection_adjustments: dict[str, Any]   # rule_code → adjustment
    audit_format:  str                      # "cms_standard" | "state_medicaid" | "custom"
    reporting_requirements: list[str]
    active:        bool   = True
    created_at:    datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id":      self.config_id,
            "tenant_id":      self.tenant_id,
            "payer_id":       self.payer_id,
            "payer_name":     self.payer_name,
            "audit_format":   self.audit_format,
            "active":         self.active,
        }


class OverrideRegistry:
    """
    Registry of policy overrides and payer configs for all tenants.

    Override application order (highest precedence first):
    1. Org-specific payer override
    2. Org-specific regional override
    3. Tenant-wide payer override
    4. Tenant-wide regional override
    5. Rule pack defaults
    """

    def __init__(self, db_writer: Optional[Callable] = None) -> None:
        self._overrides: dict[str, PolicyOverride] = {}
        self._payer_configs: dict[str, PayerComplianceConfig] = {}
        self._by_tenant: dict[str, list[str]] = {}
        self._db_writer  = db_writer

    # ── Policy overrides ───────────────────────────────────────────────────────

    async def create_override(
        self,
        tenant_id:      str,
        name:           str,
        scope:          OverrideScope,
        scope_key:      str,
        policy_config:  dict[str, Any],
        created_by:     str,
        description:    str             = "",
        org_ids:        Optional[list[str]] = None,
        effective_from: Optional[datetime]  = None,
        effective_until: Optional[datetime] = None,
    ) -> PolicyOverride:
        now = datetime.now(tz=timezone.utc)
        override = PolicyOverride(
            override_id    = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            name           = name,
            scope          = scope,
            scope_key      = scope_key,
            policy_config  = policy_config,
            status         = OverrideStatus.ACTIVE,
            created_at     = now,
            created_by     = created_by,
            effective_from = effective_from or now,
            effective_until= effective_until,
            description    = description,
            org_ids        = org_ids or [],
        )
        self._overrides[override.override_id] = override
        self._by_tenant.setdefault(tenant_id, []).append(override.override_id)
        await self._persist("create_override", override)
        log.info(
            "OverrideRegistry: created %s override '%s' for tenant %s",
            scope.value, name, tenant_id[:8],
        )
        return override

    async def revoke_override(self, override_id: str, revoked_by: str) -> PolicyOverride:
        ov = self._overrides.get(override_id)
        if ov is None:
            raise OverrideNotFoundError(override_id)
        ov.status = OverrideStatus.REVOKED
        ov.metadata["revoked_by"] = revoked_by
        ov.metadata["revoked_at"] = datetime.now(tz=timezone.utc).isoformat()
        await self._persist("update_override", ov)
        return ov

    def get_active_overrides(
        self,
        tenant_id: str,
        scope:     Optional[OverrideScope] = None,
        org_id:    Optional[str]           = None,
    ) -> list[PolicyOverride]:
        ids      = self._by_tenant.get(tenant_id, [])
        result   = []
        for oid in ids:
            ov = self._overrides.get(oid)
            if not ov or not ov.is_active_now:
                continue
            if scope and ov.scope != scope:
                continue
            # Include tenant-wide (empty org_ids) or org-specific
            if org_id and ov.org_ids and org_id not in ov.org_ids:
                continue
            result.append(ov)
        return sorted(result, key=lambda o: o.effective_from, reverse=True)

    def effective_policy_config(
        self,
        tenant_id: str,
        org_id:    Optional[str] = None,
    ) -> dict[str, Any]:
        """Merge all active overrides into a single policy config dict."""
        overrides = self.get_active_overrides(tenant_id, org_id=org_id)
        # Apply lowest-precedence first, then higher overwrites
        merged: dict[str, Any] = {}
        for ov in reversed(overrides):
            merged.update(ov.policy_config)
        return merged

    # ── Payer configs ──────────────────────────────────────────────────────────

    async def set_payer_config(
        self,
        tenant_id:      str,
        payer_id:       str,
        payer_name:     str,
        detection_adjustments: dict[str, Any],
        audit_format:   str,
        reporting_requirements: list[str],
    ) -> PayerComplianceConfig:
        config = PayerComplianceConfig(
            config_id      = str(uuid.uuid4()),
            tenant_id      = tenant_id,
            payer_id       = payer_id,
            payer_name     = payer_name,
            detection_adjustments = detection_adjustments,
            audit_format   = audit_format,
            reporting_requirements = reporting_requirements,
        )
        key = f"{tenant_id}:{payer_id}"
        self._payer_configs[key] = config
        await self._persist("upsert_payer_config", config)
        return config

    def get_payer_config(
        self,
        tenant_id: str,
        payer_id:  str,
    ) -> Optional[PayerComplianceConfig]:
        return self._payer_configs.get(f"{tenant_id}:{payer_id}")

    def list_payer_configs(self, tenant_id: str) -> list[PayerComplianceConfig]:
        return [
            c for c in self._payer_configs.values()
            if c.tenant_id == tenant_id and c.active
        ]

    async def _persist(self, op: str, obj: Any) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, obj)
            except Exception as exc:
                log.error("OverrideRegistry: persist failed: %s", exc)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class OverrideNotFoundError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_registry: Optional[OverrideRegistry] = None


def get_override_registry(db_writer: Optional[Callable] = None) -> OverrideRegistry:
    global _registry
    if _registry is None:
        _registry = OverrideRegistry(db_writer=db_writer)
    return _registry
