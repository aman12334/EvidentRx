"""
Vendor-neutral EHR connector base.

Defines the abstract interface that all EHR-specific connectors implement,
decoupling the ingestion pipeline from vendor APIs (Epic, Cerner, Meditech,
Athena, and generic FHIR-R4 implementations).

Connector hierarchy
───────────────────
  EHRConnector (ABC)
    ├── EpicConnector         — Epic FHIR R4 + proprietary APIs
    ├── CernerConnector       — Oracle/Cerner FHIR R4
    ├── MediteachConnector    — Meditech Magic / Expanse
    ├── AthenaConnector       — athenahealth REST API
    └── GenericFHIRConnector  — Any FHIR R4-compliant EHR

All connectors return data in the canonical FHIR-normalised format via
the shared FHIR normaliser, so downstream pipelines need no vendor awareness.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.interop.ehr.connector")


class EHRVendor(str, Enum):
    EPIC     = "epic"
    CERNER   = "cerner"
    MEDITECH = "meditech"
    ATHENA   = "athena"
    GENERIC  = "generic"


class EHRConnectorState(str, Enum):
    UNINITIALIZED = "uninitialized"
    INITIALIZING  = "initializing"
    HEALTHY       = "healthy"
    DEGRADED      = "degraded"
    FAILED        = "failed"
    CLOSED        = "closed"


@dataclass(frozen=True)
class EHRConnectorConfig:
    """
    Immutable, tenant-scoped EHR connector configuration.

    All secrets (tokens, client_secrets) are passed as resolved strings —
    callers are responsible for loading from a secrets manager.
    """
    connector_id:       str
    tenant_id:          str
    vendor:             EHRVendor
    base_url:           str             # FHIR base URL or vendor API root

    # Auth
    auth_type:          str             = "bearer"      # bearer | oauth2 | basic | apikey
    auth_token:         str | None   = None          # pre-resolved bearer token
    client_id:          str | None   = None          # OAuth2 client ID
    client_secret:      str | None   = None          # OAuth2 client secret (resolved)
    token_url:          str | None   = None          # OAuth2 token endpoint

    # Operational
    timeout_sec:        int             = 30
    max_retries:        int             = 3
    page_size:          int             = 200
    resource_types:     list[str]       = field(default_factory=list)
    extra:              dict[str, Any]  = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.connector_id or not self.tenant_id or not self.base_url:
            raise ValueError("connector_id, tenant_id, and base_url are required")


@dataclass
class EHRConnectorHealth:
    state:                EHRConnectorState
    last_checked:         datetime | None
    last_success:         datetime | None
    error_message:        str | None
    latency_ms:           float | None
    consecutive_failures: int              = 0


# ── Abstract EHR connector ────────────────────────────────────────────────────

class EHRConnector(ABC):
    """
    Abstract EHR connector.

    The ingestion pipeline calls:
      1. initialise()   — set up HTTP client, verify connectivity
      2. fetch()        — stream resource batches
      3. health_check() — periodic liveness probe
      4. close()        — teardown

    All concrete implementations must be safe for concurrent use from a
    single event loop (i.e. asyncio-compatible).
    """

    def __init__(self, config: EHRConnectorConfig) -> None:
        self.config   = config
        self._state   = EHRConnectorState.UNINITIALIZED
        self._health  = EHRConnectorHealth(
            state               = EHRConnectorState.UNINITIALIZED,
            last_checked        = None,
            last_success        = None,
            error_message       = None,
            latency_ms          = None,
        )

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def connector_id(self) -> str:
        return self.config.connector_id

    @property
    def tenant_id(self) -> str:
        return self.config.tenant_id

    @property
    def vendor(self) -> EHRVendor:
        return self.config.vendor

    @property
    def health(self) -> EHRConnectorHealth:
        return self._health

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def initialise(self) -> None:
        """
        Set up the connector and verify the remote EHR is reachable.

        Called once before fetch() is invoked.
        Should set state to HEALTHY on success, FAILED on error.
        """

    @abstractmethod
    async def health_check(self) -> EHRConnectorHealth:
        """
        Lightweight liveness check.

        Called periodically by the health monitor. Should not consume
        tokens or trigger expensive operations.
        """

    @abstractmethod
    async def fetch(
        self,
        resource_type: str,
        since:         datetime | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """
        Fetch FHIR resources of the given type.

        Yields batches (lists) of raw FHIR resource dicts as they arrive
        from the EHR server. If `since` is provided, only returns resources
        updated after that datetime (incremental sync).

        The caller is responsible for normalisation via the FHIR normaliser.
        """

    @abstractmethod
    async def close(self) -> None:
        """Tear down connections and release resources."""

    # ── Resource type introspection ────────────────────────────────────────────

    def resource_types(self) -> list[str]:
        """
        Return the list of resource types this connector should sync.

        Defaults to config.resource_types; override for vendor-specific limits.
        """
        return self.config.resource_types or _DEFAULT_RESOURCE_TYPES

    # ── State management ───────────────────────────────────────────────────────

    def _set_state(
        self,
        state:      EHRConnectorState,
        error:      str | None   = None,
        latency_ms: float | None = None,
    ) -> None:
        self._state = state
        now         = datetime.now(tz=UTC)
        self._health = EHRConnectorHealth(
            state               = state,
            last_checked        = now,
            last_success        = now if state == EHRConnectorState.HEALTHY else self._health.last_success,
            error_message       = error,
            latency_ms          = latency_ms,
            consecutive_failures= (
                0 if state == EHRConnectorState.HEALTHY
                else self._health.consecutive_failures + 1
            ),
        )
        log.info("EHRConnector [%s] → %s", self.connector_id, state.value)

    # ── Context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> EHRConnector:
        await self.initialise()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


# ── Generic FHIR R4 EHR connector ─────────────────────────────────────────────

class GenericFHIRConnector(EHRConnector):
    """
    Generic FHIR R4 EHR connector.

    Works with any FHIR R4-compliant server. Delegates all HTTP interaction
    to FHIRClient and uses the shared FHIRConnector from the FHIR sync module
    under the hood, adapting its interface to EHRConnector.
    """

    def __init__(self, config: EHRConnectorConfig) -> None:
        super().__init__(config)
        self._fhir_connector: Any | None = None

    async def initialise(self) -> None:
        from interoperability.base.connector import (
            ConnectorConfig,
            IngestMode,
            SourceType,
        )
        from interoperability.fhir.sync import FHIRConnector
        self._set_state(EHRConnectorState.INITIALIZING)

        fhir_config = ConnectorConfig(
            connector_id = self.connector_id,
            tenant_id    = self.tenant_id,
            source_type  = SourceType.FHIR_R4,
            ingest_mode  = IngestMode.INCREMENTAL,
            base_url     = self.config.base_url,
            extra={
                "auth_token":    self.config.auth_token,
                "page_size":     self.config.page_size,
                "resource_types": self.config.resource_types,
            },
        )
        self._fhir_connector = FHIRConnector(fhir_config)
        try:
            await self._fhir_connector.initialise()
            self._set_state(EHRConnectorState.HEALTHY)
        except Exception as e:
            self._set_state(EHRConnectorState.FAILED, str(e))
            raise

    async def health_check(self) -> EHRConnectorHealth:
        if self._fhir_connector:
            await self._fhir_connector.health_check()
        return self._health

    async def fetch(
        self,
        resource_type: str,
        since:         datetime | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        if self._fhir_connector is None:
            raise RuntimeError(f"EHRConnector [{self.connector_id}] not initialised")

        from interoperability.base.connector import SyncCursor
        cursor = SyncCursor(
            connector_id  = self.connector_id,
            tenant_id     = self.tenant_id,
            resource_type = resource_type,
            last_value    = since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None,
            last_synced   = since,
        ) if since else None

        async for batch in self._fhir_connector.fetch(resource_type, cursor):
            yield [record.raw for record in batch]

    async def close(self) -> None:
        if self._fhir_connector:
            await self._fhir_connector.close()
            self._fhir_connector = None
        self._set_state(EHRConnectorState.CLOSED)


# ── Vendor-specific stub connectors ───────────────────────────────────────────

class EpicConnector(GenericFHIRConnector):
    """
    Epic EHR connector.

    Epic exposes a FHIR R4 API; additional Epic-specific capabilities
    (MyChart patient webhooks, bulk data export) are layered via extra config.
    """

    async def initialise(self) -> None:
        log.info("EpicConnector [%s]: initialising Epic FHIR R4 connection", self.connector_id)
        await super().initialise()


class CernerConnector(GenericFHIRConnector):
    """Oracle Cerner (Millennium) FHIR R4 connector."""

    async def initialise(self) -> None:
        log.info("CernerConnector [%s]: initialising Cerner FHIR R4 connection", self.connector_id)
        await super().initialise()


class AthenaConnector(GenericFHIRConnector):
    """Athenahealth FHIR R4 connector (proprietary extensions via extra config)."""

    async def initialise(self) -> None:
        log.info("AthenaConnector [%s]: initialising athenahealth connection", self.connector_id)
        await super().initialise()


# ── Factory ───────────────────────────────────────────────────────────────────

_VENDOR_CLASSES: dict[EHRVendor, type[EHRConnector]] = {
    EHRVendor.EPIC:    EpicConnector,
    EHRVendor.CERNER:  CernerConnector,
    EHRVendor.ATHENA:  AthenaConnector,
    EHRVendor.GENERIC: GenericFHIRConnector,
}


def build_ehr_connector(config: EHRConnectorConfig) -> EHRConnector:
    """
    Instantiate the correct EHRConnector subclass for a given config.

    Falls back to GenericFHIRConnector for unknown vendors.
    """
    cls = _VENDOR_CLASSES.get(config.vendor, GenericFHIRConnector)
    return cls(config)


_DEFAULT_RESOURCE_TYPES = [
    "Patient",
    "Encounter",
    "MedicationRequest",
    "MedicationDispense",
    "Claim",
    "Coverage",
    "Practitioner",
    "Organization",
]
