"""
Connector registry — central catalogue of all active connectors per tenant.

The registry is the single source of truth for:
  - Which connectors are configured for which tenants
  - Current connector health status
  - Connector lifecycle operations (register / disable / replace)

Thread safety: asyncio.Lock protects all mutations.
Tenant isolation: connectors are partitioned by tenant_id at storage level.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from interoperability.base.connector import (
    BaseConnector,
    ConnectorHealth,
    ConnectorState,
    SourceType,
)

log = logging.getLogger("evidentrx.interop.registry")


class ConnectorRegistry:
    """
    Thread-safe in-process registry of connector instances.

    In production, registry metadata (configs, health snapshots) should also
    be persisted to the interop.connector_configs table so state survives
    restarts. The in-memory dict is the fast lookup path.
    """

    def __init__(self) -> None:
        # {tenant_id: {connector_id: BaseConnector}}
        self._connectors: dict[str, dict[str, BaseConnector]] = {}
        self._lock        = asyncio.Lock()

    # ── Registration ──────────────────────────────────────────────────────────

    async def register(self, connector: BaseConnector) -> None:
        """
        Add a connector to the registry.
        Replaces any existing connector with the same connector_id.
        """
        async with self._lock:
            tenant_bucket = self._connectors.setdefault(connector.tenant_id, {})
            if connector.connector_id in tenant_bucket:
                log.warning(
                    "Replacing existing connector %s for tenant %s",
                    connector.connector_id, connector.tenant_id,
                )
                old = tenant_bucket[connector.connector_id]
                try:
                    await old.close()
                except Exception as e:
                    log.warning("Error closing old connector %s: %s", old.connector_id, e)

            tenant_bucket[connector.connector_id] = connector
            log.info(
                "Registered connector %s (type=%s tenant=%s)",
                connector.connector_id,
                connector.source_type.value,
                connector.tenant_id,
            )

    async def deregister(self, connector_id: str, tenant_id: str) -> bool:
        """Remove and close a connector. Returns True if it existed."""
        async with self._lock:
            tenant_bucket = self._connectors.get(tenant_id, {})
            connector     = tenant_bucket.pop(connector_id, None)
            if connector is None:
                return False
            try:
                await connector.close()
            except Exception as e:
                log.warning("Error closing connector %s during deregister: %s", connector_id, e)
            log.info("Deregistered connector %s (tenant=%s)", connector_id, tenant_id)
            return True

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, connector_id: str, tenant_id: str) -> BaseConnector | None:
        """Return the connector or None. Does not acquire the lock (read-only)."""
        return self._connectors.get(tenant_id, {}).get(connector_id)

    def get_all(self, tenant_id: str) -> list[BaseConnector]:
        """Return all connectors for a tenant."""
        return list(self._connectors.get(tenant_id, {}).values())

    def get_by_type(
        self,
        source_type: SourceType,
        tenant_id:   str,
    ) -> list[BaseConnector]:
        """Return all connectors of a given source type for a tenant."""
        return [
            c for c in self._connectors.get(tenant_id, {}).values()
            if c.source_type == source_type
        ]

    def get_healthy(self, tenant_id: str) -> list[BaseConnector]:
        """Return only HEALTHY connectors for a tenant."""
        return [
            c for c in self._connectors.get(tenant_id, {}).values()
            if c.state == ConnectorState.HEALTHY
        ]

    # ── Health ────────────────────────────────────────────────────────────────

    async def health_check_all(self, tenant_id: str) -> list[ConnectorHealth]:
        """
        Run health checks on all connectors for a tenant concurrently.
        Returns health snapshots regardless of individual check outcomes.
        """
        connectors = self.get_all(tenant_id)
        if not connectors:
            return []

        async def _check(c: BaseConnector) -> ConnectorHealth:
            try:
                return await c.health_check()
            except Exception as e:
                log.warning("Health check failed for %s: %s", c.connector_id, e)
                return ConnectorHealth(
                    connector_id = c.connector_id,
                    state        = ConnectorState.FAILED,
                    last_checked = datetime.now(tz=UTC),
                    error_message= str(e),
                )

        results = await asyncio.gather(*[_check(c) for c in connectors])
        return list(results)

    def snapshot(self) -> dict[str, list[dict]]:
        """
        Return a JSON-serialisable health snapshot for all tenants.
        Used by the /api/v1/interop/health endpoint.
        """
        out: dict[str, list[dict]] = {}
        for tenant_id, bucket in self._connectors.items():
            out[tenant_id] = [
                {
                    "connector_id":  c.connector_id,
                    "source_type":   c.source_type.value,
                    "state":         c.state.value,
                    "last_success":  (c.health.last_success.isoformat()
                                      if c.health.last_success else None),
                    "error_message": c.health.error_message,
                }
                for c in bucket.values()
            ]
        return out

    def __len__(self) -> int:
        return sum(len(b) for b in self._connectors.values())


# Module-level singleton
connector_registry = ConnectorRegistry()
