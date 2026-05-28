"""
Tenant-level configuration management.

Each tenant (covered entity / healthcare organization) can have isolated:
  - Rule pack subscriptions
  - Model routing overrides
  - Feature flag overrides
  - Retention policy overrides
  - Audit notification settings

Tenant configs are loaded from DB at startup and cached in-process.
Changes through admin API flush the cache for the affected tenant only.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class TenantConfig:
    """Per-tenant configuration snapshot."""
    tenant_id:          str
    tenant_name:        str
    plan_tier:          str = "enterprise"   # free | professional | enterprise
    active_rule_packs:  List[str] = field(default_factory=lambda: ["340b_core"])
    max_cases:          int | None = None   # None = unlimited
    max_findings:       int | None = None
    retention_days:     int = 2555             # 7 years default
    phi_region:         str = "us-east-1"      # data residency
    sso_enabled:        bool = False
    mfa_required:       bool = True
    ip_allowlist:       List[str] = field(default_factory=list)
    contact_email:      str | None = None
    is_active:          bool = True


class TenantConfigRegistry:
    """
    In-process registry of tenant configurations.
    Thread-safe; production deployments should back this with a DB query
    with a TTL-based invalidation cache (e.g., Redis with 5-min TTL).
    """

    def __init__(self) -> None:
        self._configs: Dict[str, TenantConfig] = {}
        self._lock    = threading.Lock()

    def register(self, config: TenantConfig) -> None:
        with self._lock:
            self._configs[config.tenant_id] = config

    def get(self, tenant_id: str) -> TenantConfig | None:
        with self._lock:
            return self._configs.get(tenant_id)

    def require(self, tenant_id: str) -> TenantConfig:
        """Return TenantConfig or raise if tenant is unknown/inactive."""
        config = self.get(tenant_id)
        if not config:
            raise ValueError(f"Unknown tenant: {tenant_id!r}")
        if not config.is_active:
            raise PermissionError(f"Tenant {tenant_id!r} is not active")
        return config

    def invalidate(self, tenant_id: str) -> None:
        """Evict a tenant's config from cache (force reload on next access)."""
        with self._lock:
            self._configs.pop(tenant_id, None)

    def all_tenant_ids(self) -> List[str]:
        with self._lock:
            return list(self._configs.keys())

    def upsert(self, config: TenantConfig) -> None:
        """Insert or update a tenant config (admin operation)."""
        with self._lock:
            self._configs[config.tenant_id] = config


tenant_registry = TenantConfigRegistry()
