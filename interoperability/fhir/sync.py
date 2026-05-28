"""
FHIR incremental synchronisation engine.

Drives the full pull → normalise → ingest cycle for each resource type,
tracking per-resource cursors so restarts pick up exactly where they left off.

Sync modes
──────────
  FULL      : re-fetch everything (used on first run or forced reset)
  INCREMENTAL: _lastUpdated > cursor → fetch only new/changed resources
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from interoperability.base.connector import (
    BaseConnector,
    ConnectorConfig,
    ConnectorHealth,
    ConnectorState,
    ConnectorUnavailableError,
    IngestRecord,
    SourceType,
    SyncCursor,
)
from interoperability.fhir.client import FHIRClient, FHIRClientError
from interoperability.fhir.normalizer import NormalisationError, normalise
from interoperability.fhir.resources import RESOURCE_CATALOGUE, FHIRResourceType, supported_types
from interoperability.fhir.validator import FHIRValidator

log = logging.getLogger("evidentrx.interop.fhir.sync")


class FHIRConnector(BaseConnector):
    """
    FHIR R4 server connector.

    Wraps FHIRClient and implements the BaseConnector protocol so the
    generic IngestionPipeline can drive it without knowing FHIR specifics.

    Configuration extras (ConnectorConfig.extra)
    ────────────────────────────────────────────
      auth_token         : Bearer token (pre-resolved)
      page_size          : Override default 200
      resource_types     : List of resource types to sync (default: all supported)
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._http_client: FHIRClient | None = None
        self._validator    = FHIRValidator()
        self._cursors:     dict[str, SyncCursor] = {}

    # ── BaseConnector lifecycle ───────────────────────────────────────────────

    async def initialise(self) -> None:
        self._transition(ConnectorState.INITIALISING)
        self._http_client = FHIRClient(
            base_url    = self.config.base_url,
            auth_token  = self.config.extra.get("auth_token"),
            timeout_sec = self.config.timeout_sec,
            page_size   = self.config.extra.get("page_size", 200),
            max_retries = self.config.max_retries,
        )
        try:
            await self._http_client.get_capability_statement()
            self._transition(ConnectorState.HEALTHY)
            log.info("FHIR connector initialised: %s", self.config.base_url)
        except FHIRClientError as e:
            self._transition(ConnectorState.FAILED, str(e))
            raise ConnectorUnavailableError(self.connector_id, str(e)) from e

    async def health_check(self) -> ConnectorHealth:
        if self._http_client is None:
            self._transition(ConnectorState.FAILED, "Client not initialised")
            return self._health
        try:
            import time
            t0 = time.perf_counter()
            await self._http_client.get_capability_statement()
            latency = (time.perf_counter() - t0) * 1000
            self._transition(ConnectorState.HEALTHY, latency_ms=latency)
        except Exception as e:
            self._transition(ConnectorState.DEGRADED, str(e))
        return self._health

    async def fetch(
        self,
        resource_type: str,
        cursor:        SyncCursor | None = None,
    ) -> AsyncIterator[list[IngestRecord]]:
        if self._http_client is None:
            raise ConnectorUnavailableError(self.connector_id, "Not initialised")

        meta = RESOURCE_CATALOGUE.get(FHIRResourceType(resource_type))
        params = dict(meta.sync_params) if meta else {}

        if cursor and cursor.last_value:
            # Incremental: only fetch resources updated after last sync
            params["_lastUpdated"] = f"gt{cursor.last_value}"
            log.info(
                "Incremental FHIR sync: %s since %s",
                resource_type, cursor.last_value,
            )
        else:
            log.info("Full FHIR sync: %s", resource_type)

        async for page in self._http_client.search(resource_type, params):
            batch: list[IngestRecord] = []
            for raw_resource in page:
                try:
                    canonical = normalise(raw_resource, self.tenant_id)
                    valid, errors = self._validator.validate_canonical(canonical, resource_type)
                    if not valid:
                        log.warning(
                            "Validation failed for %s/%s: %s",
                            resource_type, raw_resource.get("id"), errors,
                        )
                        continue
                    batch.append(IngestRecord(
                        source_id     = raw_resource.get("id", ""),
                        source_type   = SourceType.FHIR_R4,
                        connector_id  = self.connector_id,
                        tenant_id     = self.tenant_id,
                        resource_type = resource_type,
                        raw           = raw_resource,
                        canonical     = canonical,
                        version       = raw_resource.get("meta", {}).get("versionId"),
                    ))
                except NormalisationError as e:
                    log.warning("Skipping malformed %s resource: %s", resource_type, e)
            if batch:
                yield batch

    async def get_cursor(self, resource_type: str) -> SyncCursor | None:
        return self._cursors.get(resource_type)

    async def save_cursor(self, cursor: SyncCursor) -> None:
        # In-memory; production implementations persist to DB
        now = datetime.now(tz=UTC)
        self._cursors[cursor.resource_type] = SyncCursor(
            connector_id  = cursor.connector_id,
            tenant_id     = cursor.tenant_id,
            resource_type = cursor.resource_type,
            last_value    = now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            last_synced   = now,
            records_total = cursor.records_total,
        )

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.close()
            self._http_client = None
        self._transition(ConnectorState.DISABLED)

    def resource_types_to_sync(self) -> list[str]:
        """Return resource types configured for this connector."""
        configured = self.config.extra.get("resource_types")
        if configured:
            return [rt for rt in configured if rt in supported_types()]
        return supported_types()
