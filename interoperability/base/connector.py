"""
Abstract connector base — the contract every integration must satisfy.

All healthcare source connectors (FHIR servers, HL7 feeds, EDI endpoints,
pharmacy systems, EHR APIs) implement this interface. The registry, pipeline
runner, and governance layer program against this contract only — never
against concrete implementations.

Connector lifecycle
───────────────────
  REGISTERED → INITIALISING → HEALTHY
                            → DEGRADED → HEALTHY (auto-recover)
                            → FAILED   → DISABLED (manual intervention)
  Any state  → DISABLED     (admin action)

Thread / async safety
─────────────────────
All methods are async. Connectors must be safe to run concurrently across
multiple asyncio tasks but need not be thread-safe (no threads are used).
"""

from __future__ import annotations

import abc
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.interop.connector")


# ── Connector state ───────────────────────────────────────────────────────────

class ConnectorState(str, Enum):
    REGISTERED   = "registered"
    INITIALISING = "initialising"
    HEALTHY      = "healthy"
    DEGRADED     = "degraded"
    FAILED       = "failed"
    DISABLED     = "disabled"


class SourceType(str, Enum):
    FHIR_R4        = "fhir_r4"
    FHIR_STU3      = "fhir_stu3"
    HL7_V2         = "hl7_v2"
    EDI_X12        = "edi_x12"
    PHARMACY_NCPDP = "pharmacy_ncpdp"
    PHARMACY_PROPRIETARY = "pharmacy_proprietary"
    EHR_EPIC       = "ehr_epic"
    EHR_CERNER     = "ehr_cerner"
    EHR_MEDITECH   = "ehr_meditech"
    EHR_ATHENA     = "ehr_athena"
    EHR_GENERIC    = "ehr_generic"
    FLAT_FILE      = "flat_file"
    DATABASE_DIRECT= "database_direct"


class IngestMode(str, Enum):
    FULL_LOAD      = "full_load"       # replace all data from source
    INCREMENTAL    = "incremental"     # fetch only records changed since cursor
    CHANGE_CAPTURE = "change_capture"  # CDC / event-driven
    POLLING        = "polling"         # periodic pull on schedule
    WEBHOOK        = "webhook"         # source pushes to us


# ── Value objects ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConnectorConfig:
    """
    Immutable per-tenant connector configuration.
    Secrets (API keys, passwords) must be pre-resolved via security.secrets
    before being passed here — never stored as plaintext in DB.
    """
    connector_id:   str
    tenant_id:      str
    source_type:    SourceType
    ingest_mode:    IngestMode
    display_name:   str
    base_url:       str                     = ""
    auth_headers:   dict[str, str]          = field(default_factory=dict)
    extra:          dict[str, Any]          = field(default_factory=dict)
    timeout_sec:    int                     = 30
    max_retries:    int                     = 3
    batch_size:     int                     = 500


@dataclass
class ConnectorHealth:
    connector_id:   str
    state:          ConnectorState
    last_checked:   datetime
    last_success:   datetime | None      = None
    error_message:  str | None           = None
    latency_ms:     float | None         = None
    consecutive_failures: int               = 0


@dataclass
class IngestRecord:
    """
    Single normalised record flowing through an ingestion pipeline.
    source_id is the primary key in the originating system.
    """
    source_id:      str
    source_type:    SourceType
    connector_id:   str
    tenant_id:      str
    resource_type:  str                     # e.g. "Patient", "MedicationDispense"
    raw:            dict[str, Any]          # verbatim from source (for lineage)
    canonical:      dict[str, Any]          # after normalization
    ingested_at:    datetime                = field(default_factory=lambda: datetime.now(tz=UTC))
    version:        str | None           = None
    checksum:       str | None           = None


@dataclass
class SyncCursor:
    """Tracks how far an incremental sync has progressed."""
    connector_id:   str
    tenant_id:      str
    resource_type:  str
    last_value:     str | None           = None   # ISO timestamp or opaque token
    last_synced:    datetime | None      = None
    records_total:  int                     = 0


# ── Abstract connector interface ──────────────────────────────────────────────

class BaseConnector(abc.ABC):
    """
    Abstract base for all healthcare source connectors.

    Implementers must override:
      - initialise()     — connect, authenticate, verify reachability
      - health_check()   — lightweight probe (e.g. GET /metadata)
      - fetch()          — core data pull; yields IngestRecord batches
      - close()          — clean teardown

    Optional overrides:
      - get_cursor()     — restore incremental sync position
      - save_cursor()    — persist progress after each batch
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self.config  = config
        self._state  = ConnectorState.REGISTERED
        self._health = ConnectorHealth(
            connector_id=config.connector_id,
            state=ConnectorState.REGISTERED,
            last_checked=datetime.now(tz=UTC),
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def connector_id(self) -> str:
        return self.config.connector_id

    @property
    def tenant_id(self) -> str:
        return self.config.tenant_id

    @property
    def source_type(self) -> SourceType:
        return self.config.source_type

    @property
    def state(self) -> ConnectorState:
        return self._state

    @property
    def health(self) -> ConnectorHealth:
        return self._health

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def initialise(self) -> None:
        """
        Establish connection, authenticate, verify endpoint reachability.
        Called once before any fetch(). Must set state to HEALTHY on success
        or raise ConnectorError on failure.
        """

    @abc.abstractmethod
    async def health_check(self) -> ConnectorHealth:
        """
        Lightweight reachability probe. Must complete within timeout_sec.
        Returns current health — does NOT raise.
        """

    @abc.abstractmethod
    async def fetch(
        self,
        resource_type: str,
        cursor:        SyncCursor | None = None,
    ) -> AsyncIterator[list[IngestRecord]]:
        """
        Core pull — yields batches of IngestRecord.
        For incremental mode: fetch only records modified after cursor.last_value.
        For full load: fetch all records.
        Each yielded list is one page / batch.
        """

    @abc.abstractmethod
    async def close(self) -> None:
        """Release connections and resources. Idempotent."""

    # ── Cursor management (override for stateful connectors) ──────────────────

    async def get_cursor(self, resource_type: str) -> SyncCursor | None:
        """Return the last saved cursor for this resource type, or None."""
        return None

    async def save_cursor(self, cursor: SyncCursor) -> None:
        """Persist cursor after processing a batch (idempotent)."""

    # ── State transitions ─────────────────────────────────────────────────────

    def _transition(
        self,
        new_state:     ConnectorState,
        error_message: str | None = None,
        latency_ms:    float | None = None,
    ) -> None:
        now = datetime.now(tz=UTC)
        self._state = new_state
        self._health = ConnectorHealth(
            connector_id=self.connector_id,
            state=new_state,
            last_checked=now,
            last_success=(now if new_state == ConnectorState.HEALTHY
                          else self._health.last_success),
            error_message=error_message,
            latency_ms=latency_ms,
            consecutive_failures=(
                0 if new_state == ConnectorState.HEALTHY
                else self._health.consecutive_failures + 1
            ),
        )
        log.info(
            "Connector %s → %s%s",
            self.connector_id, new_state.value,
            f" ({error_message})" if error_message else "",
        )

    # ── Async context manager ─────────────────────────────────────────────────

    async def __aenter__(self) -> BaseConnector:
        await self.initialise()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"id={self.connector_id!r} "
            f"type={self.source_type.value!r} "
            f"state={self._state.value!r}>"
        )


# ── Exceptions ────────────────────────────────────────────────────────────────

class ConnectorError(Exception):
    """Base for all connector-layer errors."""
    def __init__(self, connector_id: str, detail: str) -> None:
        super().__init__(f"[{connector_id}] {detail}")
        self.connector_id = connector_id
        self.detail       = detail


class ConnectorAuthError(ConnectorError):
    """Authentication / authorisation failure at the source system."""


class ConnectorTimeoutError(ConnectorError):
    """Source did not respond within the configured timeout."""


class ConnectorDataError(ConnectorError):
    """Source returned malformed or unexpected data."""


class ConnectorUnavailableError(ConnectorError):
    """Source system is temporarily unavailable."""
