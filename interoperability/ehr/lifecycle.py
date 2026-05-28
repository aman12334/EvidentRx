"""
EHR connector lifecycle management.

Manages the full lifecycle of EHR connectors for all tenants:
  - Instantiation from config registry
  - Initialisation (with retry on transient failure)
  - Background health monitoring
  - Graceful shutdown and cleanup

This module is the single point of control for EHR connector state in
production. It bridges the EHRConfigRegistry (config) and the
ConnectorRegistry (runtime state) from interoperability/base/registry.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing   import Any, Optional

from interoperability.ehr.config    import EHRConfigRegistry, EHRConnectorConfig
from interoperability.ehr.connector import (
    EHRConnector,
    EHRConnectorState,
    build_ehr_connector,
)

log = logging.getLogger("evidentrx.interop.ehr.lifecycle")

_INIT_RETRY_DELAY_SEC = 30
_HEALTH_CHECK_INTERVAL_SEC = 60


class EHRLifecycleManager:
    """
    Manages EHR connector lifecycle across all tenants.

    Usage
    ─────
      mgr = EHRLifecycleManager(config_registry)
      await mgr.start()          # initialise all connectors
      await mgr.sync_tenant(tid) # trigger on-demand sync for a tenant
      await mgr.stop()           # shutdown
    """

    def __init__(self, config_registry: EHRConfigRegistry) -> None:
        self._config_registry = config_registry
        self._connectors:     dict[str, dict[str, EHRConnector]] = {}
        # {tenant_id: {connector_id: EHRConnector}}
        self._init_lock        = asyncio.Lock()
        self._health_task:     Optional[asyncio.Task] = None
        self._started          = False

    # ── Startup / shutdown ────────────────────────────────────────────────────

    async def start(self, tenants: Optional[list[str]] = None) -> None:
        """
        Initialise EHR connectors for all (or specified) tenants.

        If tenants is None, only connectors already registered in the
        config registry cache are started.
        """
        if self._started:
            log.warning("EHRLifecycleManager.start() called when already started")
            return

        log.info("EHRLifecycleManager: starting")
        target_tenants = tenants or list(self._config_registry._cache.keys())

        for tenant_id in target_tenants:
            await self._initialise_tenant(tenant_id)

        self._health_task = asyncio.create_task(self._health_loop())
        self._started = True
        log.info("EHRLifecycleManager: started (%d tenants)", len(target_tenants))

    async def stop(self) -> None:
        """Gracefully shut down all connectors."""
        log.info("EHRLifecycleManager: stopping")

        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        # Close all connectors concurrently
        tasks = []
        for tenant_connectors in self._connectors.values():
            for connector in tenant_connectors.values():
                tasks.append(self._safe_close(connector))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._connectors.clear()
        self._started = False
        log.info("EHRLifecycleManager: stopped")

    # ── Tenant operations ──────────────────────────────────────────────────────

    async def _initialise_tenant(self, tenant_id: str) -> None:
        """Load configs and initialise connectors for one tenant."""
        configs = await self._config_registry.get_all(tenant_id)
        if not configs:
            log.debug("No EHR configs for tenant %s", tenant_id)
            return

        async with self._init_lock:
            if tenant_id not in self._connectors:
                self._connectors[tenant_id] = {}

        for config in configs:
            await self._initialise_connector(tenant_id, config)

    async def _initialise_connector(
        self,
        tenant_id: str,
        config:    EHRConnectorConfig,
        retries:   int = 2,
    ) -> Optional[EHRConnector]:
        """
        Build and initialise one EHR connector, with retry on failure.

        Returns the connector on success, None on permanent failure.
        """
        cid = config.connector_id
        try:
            connector = build_ehr_connector(config)
            await connector.initialise()
            async with self._init_lock:
                self._connectors.setdefault(tenant_id, {})[cid] = connector
            log.info(
                "EHRLifecycleManager: initialised %s/%s (%s)",
                tenant_id, cid, config.vendor.value,
            )
            return connector
        except Exception as exc:
            log.error(
                "EHRLifecycleManager: failed to initialise %s/%s: %s",
                tenant_id, cid, exc,
            )
            if retries > 0:
                log.info(
                    "EHRLifecycleManager: retrying %s/%s in %ds",
                    tenant_id, cid, _INIT_RETRY_DELAY_SEC,
                )
                await asyncio.sleep(_INIT_RETRY_DELAY_SEC)
                return await self._initialise_connector(tenant_id, config, retries - 1)
            return None

    # ── Connector access ───────────────────────────────────────────────────────

    def get_connector(
        self,
        tenant_id:    str,
        connector_id: str,
    ) -> Optional[EHRConnector]:
        return self._connectors.get(tenant_id, {}).get(connector_id)

    def get_tenant_connectors(self, tenant_id: str) -> list[EHRConnector]:
        return list(self._connectors.get(tenant_id, {}).values())

    def get_healthy_connectors(self, tenant_id: str) -> list[EHRConnector]:
        return [
            c for c in self.get_tenant_connectors(tenant_id)
            if c.health.state == EHRConnectorState.HEALTHY
        ]

    # ── Health monitoring ──────────────────────────────────────────────────────

    async def _health_loop(self) -> None:
        """Background task: periodic health checks on all connectors."""
        while True:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL_SEC)
            try:
                await self._run_health_checks()
            except Exception as exc:
                log.error("EHRLifecycleManager: health loop error: %s", exc)

    async def _run_health_checks(self) -> None:
        all_connectors = [
            (tid, cid, connector)
            for tid, tenant_map in self._connectors.items()
            for cid, connector in tenant_map.items()
        ]
        if not all_connectors:
            return

        async def _check(tid: str, cid: str, c: EHRConnector) -> None:
            try:
                health = await asyncio.wait_for(c.health_check(), timeout=15)
                if health.state == EHRConnectorState.FAILED:
                    log.warning(
                        "EHRLifecycleManager: %s/%s FAILED — attempting reinit",
                        tid, cid,
                    )
                    config = await self._config_registry.get(tid, cid)
                    if config:
                        await self._initialise_connector(tid, config)
            except Exception as exc:
                log.warning("EHRLifecycleManager: health check %s/%s: %s", tid, cid, exc)

        await asyncio.gather(
            *[_check(tid, cid, c) for tid, cid, c in all_connectors],
            return_exceptions=True,
        )

    # ── Cleanup helper ─────────────────────────────────────────────────────────

    @staticmethod
    async def _safe_close(connector: EHRConnector) -> None:
        try:
            await connector.close()
        except Exception as exc:
            log.warning("EHRLifecycleManager: error closing %s: %s", connector.connector_id, exc)

    # ── Status snapshot ────────────────────────────────────────────────────────

    def status_snapshot(self) -> dict[str, Any]:
        """Return a dict summary of all connector states for observability."""
        return {
            tenant_id: [
                {
                    "connector_id": c.connector_id,
                    "vendor":       c.vendor.value,
                    "state":        c.health.state.value,
                    "last_checked": c.health.last_checked.isoformat() if c.health.last_checked else None,
                    "latency_ms":   c.health.latency_ms,
                    "failures":     c.health.consecutive_failures,
                }
                for c in tenant_map.values()
            ]
            for tenant_id, tenant_map in self._connectors.items()
        }
