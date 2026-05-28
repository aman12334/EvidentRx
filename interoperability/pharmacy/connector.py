"""
Pharmacy data connector abstraction.

Provides a vendor-neutral interface for connecting to pharmacy data sources:
  - Retail pharmacy chains (NCPDP SFTP feeds)
  - Pharmacy benefit managers (PBM API / SFTP)
  - Specialty pharmacy platforms (proprietary APIs)
  - Hospital outpatient pharmacy systems

All concrete connectors extend PharmacyConnector and implement the
pull() coroutine which yields batches of canonical dispense records.

Supported feed types
────────────────────
  NCPDP_BATCH      : Standard NCPDP D.0 flat-file batch feed via SFTP
  NCPDP_REALTIME   : NCPDP switch real-time adjudication stream
  PBM_API          : PBM-specific REST/SOAP API
  SFTP_PROPRIETARY : Non-standard proprietary flat file over SFTP
  DATABASE_DIRECT  : Direct DB connection (hospital ODS / data warehouse)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.interop.pharmacy.connector")


class PharmacyFeedType(str, Enum):
    NCPDP_BATCH       = "ncpdp_batch"
    NCPDP_REALTIME    = "ncpdp_realtime"
    PBM_API           = "pbm_api"
    SFTP_PROPRIETARY  = "sftp_proprietary"
    DATABASE_DIRECT   = "database_direct"


class PharmacyConnectorState(str, Enum):
    IDLE       = "idle"
    CONNECTING = "connecting"
    ACTIVE     = "active"
    PAUSED     = "paused"
    ERROR      = "error"
    CLOSED     = "closed"


@dataclass(frozen=True)
class PharmacyConnectorConfig:
    """
    Immutable configuration for a pharmacy data connector.

    Credentials are expected to be resolved from a secrets store before
    instantiation — no secrets stored in plaintext here.
    """
    connector_id:    str
    tenant_id:       str
    feed_type:       PharmacyFeedType
    display_name:    str

    # Connection details (feed-type dependent)
    host:            str | None           = None
    port:            int                     = 22          # SFTP default
    username:        str | None           = None
    password:        str | None           = None        # resolved from secrets
    api_key:         str | None           = None
    base_url:        str | None           = None

    # Operational settings
    batch_size:      int                     = 500
    timeout_sec:     int                     = 30
    max_retries:     int                     = 3
    poll_interval_s: int                     = 300         # 5 minutes
    extra:           dict[str, Any]          = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.connector_id:
            raise ValueError("connector_id is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")


@dataclass
class PullResult:
    """Summary of a single pull operation."""
    connector_id:    str
    started_at:      datetime
    finished_at:     datetime
    records_fetched: int
    records_failed:  int
    errors:          list[str]         = field(default_factory=list)
    is_partial:      bool              = False

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def success_rate(self) -> float:
        total = self.records_fetched + self.records_failed
        return (self.records_fetched / total) if total > 0 else 1.0


# ── Abstract base connector ───────────────────────────────────────────────────

class PharmacyConnector(ABC):
    """
    Abstract base for all pharmacy data connectors.

    Subclasses implement pull() to fetch dispense records from a specific
    pharmacy data source and yield them as canonical dispense dicts.
    """

    def __init__(self, config: PharmacyConnectorConfig) -> None:
        self.config    = config
        self._state    = PharmacyConnectorState.IDLE
        self._last_pull: datetime | None = None
        self._pull_count: int = 0

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def connector_id(self) -> str:
        return self.config.connector_id

    @property
    def tenant_id(self) -> str:
        return self.config.tenant_id

    @property
    def state(self) -> PharmacyConnectorState:
        return self._state

    @property
    def feed_type(self) -> PharmacyFeedType:
        return self.config.feed_type

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the data source. Sets state to ACTIVE."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection gracefully. Sets state to CLOSED."""

    @abstractmethod
    async def pull(
        self,
        since: datetime | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """
        Fetch dispense records from the pharmacy source.

        Yields batches of canonical dispense dicts. If `since` is provided,
        fetches only records updated after that timestamp (incremental).
        """

    @abstractmethod
    async def ping(self) -> bool:
        """
        Lightweight connectivity check.

        Returns True if the source is reachable, False otherwise.
        Does not raise.
        """

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _set_state(self, state: PharmacyConnectorState) -> None:
        old = self._state
        self._state = state
        if old != state:
            log.info(
                "PharmacyConnector [%s] %s → %s",
                self.connector_id, old.value, state.value,
            )

    async def __aenter__(self) -> PharmacyConnector:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()


# ── SFTP-based NCPDP batch connector (concrete reference implementation) ──────

class NCPDPBatchConnector(PharmacyConnector):
    """
    NCPDP D.0 batch file connector via SFTP.

    Polls a remote SFTP directory for new dispense batch files,
    parses each file, and yields canonical dispense records.

    File naming convention expected: <facility>_<YYYYMMDD>_<seq>.txt
    Records are fixed-width NCPDP D.0 transaction sets.
    """

    def __init__(self, config: PharmacyConnectorConfig) -> None:
        super().__init__(config)
        self._sftp_client: Any | None = None

    async def connect(self) -> None:
        self._set_state(PharmacyConnectorState.CONNECTING)
        log.info(
            "NCPDPBatchConnector [%s]: connecting to %s:%d",
            self.connector_id, self.config.host, self.config.port,
        )
        # In production: self._sftp_client = await asyncssh.connect(...)
        # For platform integration, the real implementation uses asyncssh
        self._set_state(PharmacyConnectorState.ACTIVE)

    async def disconnect(self) -> None:
        if self._sftp_client:
            self._sftp_client = None
        self._set_state(PharmacyConnectorState.CLOSED)

    async def pull(
        self,
        since: datetime | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """
        List remote directory, download new files, parse and yield records.

        Files already seen (tracked by cursor) are skipped to ensure
        idempotent behaviour on retry.
        """
        self._set_state(PharmacyConnectorState.ACTIVE)
        self._last_pull = datetime.now(tz=UTC)
        self._pull_count += 1

        log.info(
            "NCPDPBatchConnector [%s]: pulling%s",
            self.connector_id,
            f" since {since.isoformat()}" if since else " (full)",
        )

        # Placeholder — real implementation lists SFTP dir and parses NCPDP files
        # Each file yields a batch of canonical dispense dicts
        if False:   # pragma: no cover
            yield []   # satisfy AsyncIterator type

    async def ping(self) -> bool:
        try:
            if self._sftp_client is None:
                return False
            return True
        except Exception:
            return False


# ── PBM API connector (reference stub) ───────────────────────────────────────

class PBMAPIConnector(PharmacyConnector):
    """
    Pharmacy Benefit Manager REST API connector.

    Calls the PBM's /claims or /dispenses endpoint with date-range filters
    and paginates through results. Each PBM has its own API — the specific
    request/response mapping is injected via config.extra["api_mapping"].
    """

    def __init__(self, config: PharmacyConnectorConfig) -> None:
        super().__init__(config)
        self._http_client: Any | None = None

    async def connect(self) -> None:
        self._set_state(PharmacyConnectorState.CONNECTING)
        # In production: self._http_client = httpx.AsyncClient(...)
        self._set_state(PharmacyConnectorState.ACTIVE)

    async def disconnect(self) -> None:
        if self._http_client:
            self._http_client = None
        self._set_state(PharmacyConnectorState.CLOSED)

    async def pull(
        self,
        since: datetime | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        log.info(
            "PBMAPIConnector [%s]: pulling from %s",
            self.connector_id, self.config.base_url,
        )
        if False:   # pragma: no cover
            yield []

    async def ping(self) -> bool:
        try:
            # HEAD request to base_url/health or similar
            return self._http_client is not None
        except Exception:
            return False


# ── Factory ───────────────────────────────────────────────────────────────────

_CONNECTOR_CLASSES: dict[PharmacyFeedType, type[PharmacyConnector]] = {
    PharmacyFeedType.NCPDP_BATCH:    NCPDPBatchConnector,
    PharmacyFeedType.PBM_API:        PBMAPIConnector,
}


def build_pharmacy_connector(config: PharmacyConnectorConfig) -> PharmacyConnector:
    """
    Instantiate the correct PharmacyConnector subclass for a given config.

    Raises ValueError for unsupported feed types.
    """
    cls = _CONNECTOR_CLASSES.get(config.feed_type)
    if cls is None:
        raise ValueError(
            f"No pharmacy connector implementation for feed type {config.feed_type!r}. "
            f"Supported: {list(_CONNECTOR_CLASSES)}"
        )
    return cls(config)
