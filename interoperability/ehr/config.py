"""
Tenant-specific EHR connector configuration management.

Provides a registry of per-tenant EHR connector configurations, supporting:
  - Multi-tenant isolation (each tenant has its own connector set)
  - Dynamic config reloading without service restart
  - Schema validation at registration time
  - Secrets reference resolution (placeholder → resolved by secrets manager)

Configuration is stored in the database (interop.connector_configs) and
cached in-memory with a TTL-based refresh. This module owns the in-memory
cache and DB read layer; the DB write layer is in governance/policy.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from interoperability.ehr.connector import EHRConnectorConfig, EHRVendor

log = logging.getLogger("evidentrx.interop.ehr.config")

_CACHE_TTL_SECONDS = 300    # 5-minute cache TTL


@dataclass
class ConfigCacheEntry:
    config:      EHRConnectorConfig
    loaded_at:   datetime
    ttl_seconds: int = _CACHE_TTL_SECONDS

    @property
    def is_expired(self) -> bool:
        return datetime.now(tz=UTC) > self.loaded_at + timedelta(seconds=self.ttl_seconds)


class EHRConfigRegistry:
    """
    In-memory cache of per-tenant EHR connector configurations.

    In production, configurations are loaded from the database and refreshed
    on TTL expiry. Secrets (tokens, client_secrets) are resolved by the
    secrets manager before being stored here.
    """

    def __init__(self, db_loader: Any | None = None) -> None:
        """
        Parameters
        ----------
        db_loader : async callable(tenant_id) → list[dict]
            Loads connector configs for a tenant from the database.
            Each dict must map to EHRConnectorConfig fields.
            If None, only manually-registered configs are available.
        """
        self._cache:     dict[str, dict[str, ConfigCacheEntry]] = {}
        # {tenant_id: {connector_id: ConfigCacheEntry}}
        self._db_loader  = db_loader

    # ── Manual registration ────────────────────────────────────────────────────

    def register(self, config: EHRConnectorConfig) -> None:
        """
        Register a connector config directly (e.g. from tests or seed data).
        Overrides any cached DB-loaded config with the same connector_id.
        """
        tenant = config.tenant_id
        if tenant not in self._cache:
            self._cache[tenant] = {}
        self._cache[tenant][config.connector_id] = ConfigCacheEntry(
            config    = config,
            loaded_at = datetime.now(tz=UTC),
        )
        log.info(
            "EHRConfigRegistry: registered %s/%s (%s)",
            tenant, config.connector_id, config.vendor.value,
        )

    def deregister(self, tenant_id: str, connector_id: str) -> None:
        """Remove a config from the cache (does not delete from DB)."""
        self._cache.get(tenant_id, {}).pop(connector_id, None)

    # ── Lookup ─────────────────────────────────────────────────────────────────

    async def get(
        self,
        tenant_id:    str,
        connector_id: str,
    ) -> EHRConnectorConfig | None:
        """
        Return config for a specific connector, refreshing from DB if expired.
        """
        tenant_cache = self._cache.get(tenant_id, {})
        entry = tenant_cache.get(connector_id)

        if entry is None or entry.is_expired:
            await self._refresh_tenant(tenant_id)
            tenant_cache = self._cache.get(tenant_id, {})
            entry = tenant_cache.get(connector_id)

        return entry.config if entry else None

    async def get_all(self, tenant_id: str) -> list[EHRConnectorConfig]:
        """Return all connector configs for a tenant."""
        tenant_cache = self._cache.get(tenant_id, {})

        # Refresh if any entry is expired or tenant not yet loaded
        if not tenant_cache or any(e.is_expired for e in tenant_cache.values()):
            await self._refresh_tenant(tenant_id)
            tenant_cache = self._cache.get(tenant_id, {})

        return [e.config for e in tenant_cache.values()]

    def get_cached(self, tenant_id: str) -> list[EHRConnectorConfig]:
        """
        Return currently-cached configs without DB refresh.

        Safe to call from synchronous contexts. May be stale.
        """
        return [e.config for e in self._cache.get(tenant_id, {}).values()]

    # ── Refresh ────────────────────────────────────────────────────────────────

    async def _refresh_tenant(self, tenant_id: str) -> None:
        """Load/refresh all connector configs for a tenant from the DB."""
        if self._db_loader is None:
            return

        try:
            rows = await self._db_loader(tenant_id)
            loaded_at = datetime.now(tz=UTC)
            tenant_cache: dict[str, ConfigCacheEntry] = {}

            for row in rows:
                try:
                    config = _row_to_config(row)
                    tenant_cache[config.connector_id] = ConfigCacheEntry(
                        config    = config,
                        loaded_at = loaded_at,
                    )
                except Exception as exc:
                    log.warning(
                        "EHRConfigRegistry: failed to parse config row %s: %s",
                        row.get("connector_id"), exc,
                    )

            # Merge: keep manually-registered entries not in DB
            existing = self._cache.get(tenant_id, {})
            for cid, entry in existing.items():
                if cid not in tenant_cache:
                    tenant_cache[cid] = entry

            self._cache[tenant_id] = tenant_cache
            log.info(
                "EHRConfigRegistry: loaded %d configs for tenant %s",
                len(tenant_cache), tenant_id,
            )
        except Exception as exc:
            log.error(
                "EHRConfigRegistry: DB refresh failed for tenant %s: %s",
                tenant_id, exc,
            )

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return a dict representation of the full cache for debugging."""
        return {
            tenant_id: [
                {
                    "connector_id": e.config.connector_id,
                    "vendor":       e.config.vendor.value,
                    "base_url":     e.config.base_url,
                    "loaded_at":    e.loaded_at.isoformat(),
                    "expired":      e.is_expired,
                }
                for e in entries.values()
            ]
            for tenant_id, entries in self._cache.items()
        }


# ── Row deserialisation ────────────────────────────────────────────────────────

def _row_to_config(row: dict[str, Any]) -> EHRConnectorConfig:
    """Convert a DB row dict to an EHRConnectorConfig."""
    extra = row.get("extra") or {}
    return EHRConnectorConfig(
        connector_id  = row["connector_id"],
        tenant_id     = row["tenant_id"],
        vendor        = EHRVendor(row.get("vendor", "generic")),
        base_url      = row["base_url"],
        auth_type     = row.get("auth_type", "bearer"),
        auth_token    = extra.get("auth_token"),
        client_id     = extra.get("client_id"),
        client_secret = extra.get("client_secret"),
        token_url     = extra.get("token_url"),
        timeout_sec   = int(row.get("timeout_sec", 30)),
        max_retries   = int(row.get("max_retries", 3)),
        page_size     = int(row.get("page_size", 200)),
        resource_types= row.get("resource_types") or [],
        extra         = extra,
    )


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: EHRConfigRegistry | None = None


def get_ehr_config_registry(db_loader: Any | None = None) -> EHRConfigRegistry:
    """Return the module-level EHRConfigRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = EHRConfigRegistry(db_loader=db_loader)
    return _registry
