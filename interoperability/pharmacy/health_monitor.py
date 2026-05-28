"""
Pharmacy connector health monitoring.

Tracks connectivity, pull success rates, and data freshness across all
registered pharmacy connectors. Exposes health summaries to Prometheus
and provides alerting thresholds for operations teams.

Design
──────
  - Non-blocking: health checks run in background tasks
  - Threshold-based: configurable staleness and error rate thresholds
  - Aggregated: per-connector + fleet-wide summary
  - Prometheus-ready: metrics registered on startup via register_metrics()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

from interoperability.pharmacy.connector import (
    PharmacyConnector,
    PullResult,
)

log = logging.getLogger("evidentrx.interop.pharmacy.health_monitor")


# ── Health status ─────────────────────────────────────────────────────────────

class ConnectorHealthStatus(str, Enum):
    HEALTHY  = "healthy"
    DEGRADED = "degraded"
    STALE    = "stale"      # data not refreshed within SLA
    DOWN     = "down"
    UNKNOWN  = "unknown"


@dataclass
class ConnectorHealthSnapshot:
    connector_id:    str
    tenant_id:       str
    feed_type:       str
    status:          ConnectorHealthStatus
    last_checked:    datetime | None
    last_success:    datetime | None
    last_pull_count: int
    error_rate:      float              # 0.0 – 1.0 over recent window
    is_reachable:    bool
    message:         str                = ""
    consecutive_failures: int           = 0


@dataclass
class FleetHealthSummary:
    healthy_count:  int
    degraded_count: int
    down_count:     int
    stale_count:    int
    total:          int
    checked_at:     datetime            = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def all_healthy(self) -> bool:
        return self.total > 0 and self.healthy_count == self.total

    @property
    def health_fraction(self) -> float:
        return self.healthy_count / self.total if self.total > 0 else 0.0


# ── Monitor ───────────────────────────────────────────────────────────────────

class PharmacyHealthMonitor:
    """
    Background health monitor for pharmacy connectors.

    Runs periodic ping checks and tracks pull result history to compute
    per-connector health status and fleet-level aggregates.
    """

    # Default thresholds
    STALE_THRESHOLD_HOURS   = 24       # consider connector stale after 24h no data
    ERROR_RATE_THRESHOLD    = 0.25     # >25% error rate → degraded
    FAILURE_DOWN_THRESHOLD  = 5        # 5 consecutive failures → down
    HISTORY_WINDOW          = 20       # number of recent pull results to track

    def __init__(
        self,
        stale_hours:          int   = STALE_THRESHOLD_HOURS,
        error_rate_threshold: float = ERROR_RATE_THRESHOLD,
        failure_down_threshold: int = FAILURE_DOWN_THRESHOLD,
    ) -> None:
        self._connectors:  dict[str, PharmacyConnector] = {}
        self._pull_history: dict[str, list[PullResult]] = {}
        self._snapshots:   dict[str, ConnectorHealthSnapshot] = {}
        self._stale_hours  = stale_hours
        self._err_threshold= error_rate_threshold
        self._fail_threshold= failure_down_threshold
        self._check_task:  asyncio.Task | None = None

    # ── Registration ───────────────────────────────────────────────────────────

    def register(self, connector: PharmacyConnector) -> None:
        cid = connector.connector_id
        self._connectors[cid]   = connector
        self._pull_history[cid] = []
        log.info("HealthMonitor: registered connector %s", cid)

    def deregister(self, connector_id: str) -> None:
        self._connectors.pop(connector_id, None)
        self._pull_history.pop(connector_id, None)
        self._snapshots.pop(connector_id, None)

    # ── Pull result recording ──────────────────────────────────────────────────

    def record_pull(self, result: PullResult) -> None:
        """Called by the ingestion pipeline after each pull attempt."""
        cid = result.connector_id
        if cid not in self._pull_history:
            self._pull_history[cid] = []
        history = self._pull_history[cid]
        history.append(result)
        # Keep only the last HISTORY_WINDOW results
        if len(history) > self.HISTORY_WINDOW:
            del history[0]

    # ── Health check ───────────────────────────────────────────────────────────

    async def check_all(self) -> FleetHealthSummary:
        """Run ping checks on all registered connectors and update snapshots."""
        tasks = [
            self._check_one(cid, connector)
            for cid, connector in self._connectors.items()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        counts = {s: 0 for s in ConnectorHealthStatus}
        for snap in self._snapshots.values():
            counts[snap.status] = counts.get(snap.status, 0) + 1

        return FleetHealthSummary(
            healthy_count  = counts.get(ConnectorHealthStatus.HEALTHY,  0),
            degraded_count = counts.get(ConnectorHealthStatus.DEGRADED, 0),
            down_count     = counts.get(ConnectorHealthStatus.DOWN,     0),
            stale_count    = counts.get(ConnectorHealthStatus.STALE,    0),
            total          = len(self._connectors),
        )

    async def _check_one(self, cid: str, connector: PharmacyConnector) -> None:
        """Ping one connector and compute its health snapshot."""
        now = datetime.now(tz=UTC)
        try:
            is_reachable = await asyncio.wait_for(connector.ping(), timeout=10)
        except Exception:
            is_reachable = False

        history = self._pull_history.get(cid, [])
        status, message, consecutive_failures = self._compute_status(
            cid          = cid,
            is_reachable = is_reachable,
            history      = history,
            now          = now,
        )

        last_success: datetime | None = None
        last_count   = 0
        if history:
            for r in reversed(history):
                if r.records_fetched > 0:
                    last_success = r.finished_at
                    last_count   = r.records_fetched
                    break

        self._snapshots[cid] = ConnectorHealthSnapshot(
            connector_id         = cid,
            tenant_id            = connector.tenant_id,
            feed_type            = connector.feed_type.value,
            status               = status,
            last_checked         = now,
            last_success         = last_success,
            last_pull_count      = last_count,
            error_rate           = self._error_rate(history),
            is_reachable         = is_reachable,
            message              = message,
            consecutive_failures = consecutive_failures,
        )

    def _compute_status(
        self,
        cid:          str,
        is_reachable: bool,
        history:      list[PullResult],
        now:          datetime,
    ) -> tuple[ConnectorHealthStatus, str, int]:
        """Return (status, message, consecutive_failures)."""
        if not is_reachable:
            return ConnectorHealthStatus.DOWN, "Connector unreachable", len(history)

        err_rate = self._error_rate(history)
        consecutive = self._consecutive_failures(history)

        if consecutive >= self._fail_threshold:
            return (
                ConnectorHealthStatus.DOWN,
                f"{consecutive} consecutive pull failures",
                consecutive,
            )

        if err_rate >= self._err_threshold:
            return (
                ConnectorHealthStatus.DEGRADED,
                f"Error rate {err_rate:.0%} exceeds threshold",
                consecutive,
            )

        # Check staleness
        stale_cutoff = now - timedelta(hours=self._stale_hours)
        if history:
            last_finished = history[-1].finished_at
            if last_finished < stale_cutoff:
                return (
                    ConnectorHealthStatus.STALE,
                    f"No successful pull since {last_finished.isoformat()}",
                    0,
                )
        elif cid in self._connectors:
            # Registered but never pulled
            return (
                ConnectorHealthStatus.UNKNOWN,
                "Never pulled",
                0,
            )

        return ConnectorHealthStatus.HEALTHY, "OK", 0

    def _error_rate(self, history: list[PullResult]) -> float:
        if not history:
            return 0.0
        total_failed   = sum(r.records_failed for r in history)
        total_fetched  = sum(r.records_fetched for r in history)
        total          = total_failed + total_fetched
        return total_failed / total if total > 0 else 0.0

    def _consecutive_failures(self, history: list[PullResult]) -> int:
        count = 0
        for r in reversed(history):
            if r.records_failed > 0 and r.records_fetched == 0:
                count += 1
            else:
                break
        return count

    # ── Snapshots ──────────────────────────────────────────────────────────────

    def get_snapshot(self, connector_id: str) -> ConnectorHealthSnapshot | None:
        return self._snapshots.get(connector_id)

    def all_snapshots(self) -> list[ConnectorHealthSnapshot]:
        return list(self._snapshots.values())

    # ── Background polling ─────────────────────────────────────────────────────

    async def start_background_checks(self, interval_seconds: int = 60) -> None:
        """Start a background task that runs health checks on a fixed interval."""
        async def _loop() -> None:
            while True:
                try:
                    summary = await self.check_all()
                    log.info(
                        "HealthMonitor: %d/%d connectors healthy",
                        summary.healthy_count, summary.total,
                    )
                except Exception as exc:
                    log.error("HealthMonitor: check_all failed: %s", exc)
                await asyncio.sleep(interval_seconds)

        self._check_task = asyncio.create_task(_loop())
        log.info("HealthMonitor: started background checks (interval=%ds)", interval_seconds)

    async def stop(self) -> None:
        """Cancel background check task."""
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
            self._check_task = None
