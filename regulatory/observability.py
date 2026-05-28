"""
Regulatory intelligence observability layer.

Provides structured metrics collection, health checks, and operational
visibility across all Phase 13 services.  All counters are in-process
accumulators; in production these would be exported to Prometheus or
a compatible metrics backend via a push/pull adapter.

Metrics collected
─────────────────
  ingestion.*         — document ingestion throughput, error rates, dedup hits
  diff.*              — diff computation latency, severity distribution
  drift.*             — drift scan frequency, finding counts by severity
  impact.*            — impact analysis throughput, financial risk flags
  recommendations.*   — recommendation lifecycle transitions
  timeline.*          — event append rates per tenant
  readiness.*         — score distribution, band transitions
  governance.*        — workflow open/closed, SLA breach detection
  graph.*             — node/edge counts, reachability query latency

Health signals
──────────────
  HEALTHY   — all checks pass; service is operating normally
  DEGRADED  — one or more non-critical checks failing; still operational
  UNHEALTHY — a critical check has failed; service may be impaired

Design constraints
──────────────────
- Metrics collection never raises; failures are silently swallowed and
  logged at DEBUG level to avoid disrupting the primary code path
- All metric snapshots are immutable once taken
- Health checks do NOT invoke external I/O — they inspect in-memory state only
- No LLM inference or compliance logic is embedded here
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.regulatory.observability")


# ── Health status ─────────────────────────────────────────────────────────────

class HealthStatus(str, Enum):
    HEALTHY   = "healthy"
    DEGRADED  = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheckResult:
    name:     str
    status:   HealthStatus
    message:  str               = ""
    latency_ms: float           = 0.0
    checked_at: datetime        = field(default_factory=lambda: datetime.now(tz=UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":       self.name,
            "status":     self.status.value,
            "message":    self.message,
            "latency_ms": round(self.latency_ms, 2),
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class ServiceHealth:
    """Aggregated health report across all regulatory intelligence services."""
    overall:   HealthStatus
    checks:    list[HealthCheckResult]
    assessed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def healthy_count(self) -> int:
        return sum(1 for c in self.checks if c.status == HealthStatus.HEALTHY)

    @property
    def unhealthy_count(self) -> int:
        return sum(1 for c in self.checks if c.status == HealthStatus.UNHEALTHY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall":        self.overall.value,
            "assessed_at":    self.assessed_at.isoformat(),
            "healthy_count":  self.healthy_count,
            "degraded_count": len(self.checks) - self.healthy_count - self.unhealthy_count,
            "unhealthy_count":self.unhealthy_count,
            "checks":         [c.to_dict() for c in self.checks],
        }


# ── Counter & gauge primitives ────────────────────────────────────────────────

class Counter:
    """Monotonically increasing counter."""
    def __init__(self, name: str, description: str = "") -> None:
        self.name        = name
        self.description = description
        self._value: int = 0

    def inc(self, amount: int = 1) -> None:
        self._value += amount

    @property
    def value(self) -> int:
        return self._value

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self._value, "type": "counter"}


class Gauge:
    """Current-value gauge (can go up or down)."""
    def __init__(self, name: str, description: str = "") -> None:
        self.name        = name
        self.description = description
        self._value: float = 0.0

    def set(self, value: float) -> None:
        self._value = value

    def inc(self, amount: float = 1.0) -> None:
        self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        self._value -= amount

    @property
    def value(self) -> float:
        return self._value

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self._value, "type": "gauge"}


class Histogram:
    """
    Fixed-bucket latency histogram.

    Buckets (ms): 10, 50, 100, 250, 500, 1000, 2500, 5000, +Inf
    """
    _BUCKETS = (10, 50, 100, 250, 500, 1_000, 2_500, 5_000)

    def __init__(self, name: str, description: str = "") -> None:
        self.name        = name
        self.description = description
        self._counts: dict[float, int] = {b: 0 for b in self._BUCKETS}
        self._sum:    float            = 0.0
        self._total:  int              = 0

    def observe(self, value_ms: float) -> None:
        self._total += 1
        self._sum   += value_ms
        for b in self._BUCKETS:
            if value_ms <= b:
                self._counts[b] += 1

    @property
    def mean_ms(self) -> float:
        return self._sum / self._total if self._total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":     self.name,
            "type":     "histogram",
            "total":    self._total,
            "mean_ms":  round(self.mean_ms, 2),
            "sum_ms":   round(self._sum, 2),
            "buckets":  {f"le_{k}": v for k, v in self._counts.items()},
        }


# ── Metric registry ───────────────────────────────────────────────────────────

class RegulatoryMetricsRegistry:
    """
    Central registry for all Phase 13 operational metrics.

    All metrics are scoped to the regulatory intelligence layer.
    Callers record observations; the registry accumulates and exports.
    """

    def __init__(self) -> None:
        # ── Ingestion ─────────────────────────────────────────────────────────
        self.ingestion_total       = Counter("ingestion_total",       "Total document ingestion attempts")
        self.ingestion_success     = Counter("ingestion_success",     "Successful document ingestions")
        self.ingestion_errors      = Counter("ingestion_errors",      "Failed document ingestions")
        self.ingestion_dedup_hits  = Counter("ingestion_dedup_hits",  "Ingestions skipped due to content dedup")
        self.ingestion_latency     = Histogram("ingestion_latency_ms","End-to-end ingestion latency")
        self.documents_indexed     = Gauge("documents_indexed",       "Currently indexed regulatory documents")

        # ── Diff ──────────────────────────────────────────────────────────────
        self.diffs_computed        = Counter("diffs_computed",        "Total policy diffs computed")
        self.diffs_critical        = Counter("diffs_critical",        "Diffs with CRITICAL severity")
        self.diffs_high            = Counter("diffs_high",            "Diffs with HIGH severity")
        self.diff_latency          = Histogram("diff_latency_ms",     "Diff computation latency")

        # ── Drift ─────────────────────────────────────────────────────────────
        self.drift_scans_total     = Counter("drift_scans_total",     "Total drift detection runs")
        self.drift_findings_total  = Counter("drift_findings_total",  "Total drift findings raised")
        self.drift_critical        = Counter("drift_critical_total",  "Critical drift findings")
        self.drift_high            = Counter("drift_high_total",      "High drift findings")
        self.drift_coverage_gaps   = Counter("drift_coverage_gaps",   "Coverage gap findings raised")

        # ── Impact ────────────────────────────────────────────────────────────
        self.impact_reports_total  = Counter("impact_reports_total",  "Total impact reports generated")
        self.impact_financial_flags= Counter("impact_financial_flags","Impact reports with financial risk")
        self.impact_latency        = Histogram("impact_latency_ms",   "Impact analysis latency")

        # ── Recommendations ───────────────────────────────────────────────────
        self.recs_created          = Counter("recs_created",          "Recommendations created")
        self.recs_submitted        = Counter("recs_submitted",        "Recommendations submitted for review")
        self.recs_approved         = Counter("recs_approved",         "Recommendations approved")
        self.recs_rejected         = Counter("recs_rejected",         "Recommendations rejected")
        self.recs_implemented      = Counter("recs_implemented",      "Recommendations implemented")
        self.recs_rolled_back      = Counter("recs_rolled_back",      "Recommendations rolled back")
        self.recs_pending          = Gauge("recs_pending",            "Currently pending recommendations")

        # ── Timeline ──────────────────────────────────────────────────────────
        self.timeline_events_total = Counter("timeline_events_total", "Total timeline events recorded")
        self.timeline_critical     = Counter("timeline_critical",     "Critical/high severity timeline events")

        # ── Readiness ─────────────────────────────────────────────────────────
        self.readiness_snapshots   = Counter("readiness_snapshots",   "Readiness snapshots generated")
        self.readiness_strong      = Counter("readiness_strong",      "Snapshots with STRONG band")
        self.readiness_at_risk     = Counter("readiness_at_risk",     "Snapshots with AT_RISK band")
        self.readiness_critical    = Counter("readiness_critical",    "Snapshots with CRITICAL band")

        # ── Governance ────────────────────────────────────────────────────────
        self.workflows_created     = Counter("workflows_created",     "Activation workflows created")
        self.workflows_activated   = Counter("workflows_activated",   "Documents successfully activated")
        self.workflows_rejected    = Counter("workflows_rejected",    "Activation workflows rejected")
        self.workflows_open        = Gauge("workflows_open",          "Workflows currently awaiting action")

        # ── Graph ─────────────────────────────────────────────────────────────
        self.graph_nodes_total     = Gauge("graph_nodes_total",       "Total regulatory graph nodes")
        self.graph_edges_total     = Gauge("graph_edges_total",       "Total regulatory graph edges")
        self.graph_queries_total   = Counter("graph_queries_total",   "Total graph queries executed")
        self.graph_query_latency   = Histogram("graph_query_latency_ms","Graph query latency")

        # ── Citation ──────────────────────────────────────────────────────────
        self.citations_total       = Counter("citations_total",       "Policy citations created")
        self.citations_human       = Counter("citations_human",       "Human-verified citations")

        # ── Per-tenant gauges ─────────────────────────────────────────────────
        self._tenant_doc_counts:   dict[str, int]   = defaultdict(int)
        self._tenant_pending_recs: dict[str, int]   = defaultdict(int)

        self._registered_at = datetime.now(tz=UTC)

    # ── Tenant-scoped recording ───────────────────────────────────────────────

    def record_tenant_docs(self, tenant_id: str, count: int) -> None:
        self._tenant_doc_counts[tenant_id] = count

    def record_tenant_pending_recs(self, tenant_id: str, count: int) -> None:
        self._tenant_pending_recs[tenant_id] = count

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Export all current metric values as a structured dict."""
        metrics = [
            # ingestion
            self.ingestion_total.to_dict(),
            self.ingestion_success.to_dict(),
            self.ingestion_errors.to_dict(),
            self.ingestion_dedup_hits.to_dict(),
            self.ingestion_latency.to_dict(),
            self.documents_indexed.to_dict(),
            # diff
            self.diffs_computed.to_dict(),
            self.diffs_critical.to_dict(),
            self.diffs_high.to_dict(),
            self.diff_latency.to_dict(),
            # drift
            self.drift_scans_total.to_dict(),
            self.drift_findings_total.to_dict(),
            self.drift_critical.to_dict(),
            self.drift_high.to_dict(),
            self.drift_coverage_gaps.to_dict(),
            # impact
            self.impact_reports_total.to_dict(),
            self.impact_financial_flags.to_dict(),
            self.impact_latency.to_dict(),
            # recommendations
            self.recs_created.to_dict(),
            self.recs_submitted.to_dict(),
            self.recs_approved.to_dict(),
            self.recs_rejected.to_dict(),
            self.recs_implemented.to_dict(),
            self.recs_rolled_back.to_dict(),
            self.recs_pending.to_dict(),
            # timeline
            self.timeline_events_total.to_dict(),
            self.timeline_critical.to_dict(),
            # readiness
            self.readiness_snapshots.to_dict(),
            self.readiness_strong.to_dict(),
            self.readiness_at_risk.to_dict(),
            self.readiness_critical.to_dict(),
            # governance
            self.workflows_created.to_dict(),
            self.workflows_activated.to_dict(),
            self.workflows_rejected.to_dict(),
            self.workflows_open.to_dict(),
            # graph
            self.graph_nodes_total.to_dict(),
            self.graph_edges_total.to_dict(),
            self.graph_queries_total.to_dict(),
            self.graph_query_latency.to_dict(),
            # citations
            self.citations_total.to_dict(),
            self.citations_human.to_dict(),
        ]
        return {
            "snapshot_at":      datetime.now(tz=UTC).isoformat(),
            "registry_age_s":   round(
                (datetime.now(tz=UTC) - self._registered_at).total_seconds(), 1
            ),
            "metrics":          metrics,
            "tenant_doc_counts":dict(self._tenant_doc_counts),
            "tenant_pending_recs": dict(self._tenant_pending_recs),
        }

    def error_rate(self) -> float:
        total = self.ingestion_total.value
        return round(self.ingestion_errors.value / total, 4) if total else 0.0

    def dedup_rate(self) -> float:
        total = self.ingestion_total.value
        return round(self.ingestion_dedup_hits.value / total, 4) if total else 0.0


# ── Health check runner ───────────────────────────────────────────────────────

HealthCheckFn = Callable[[], HealthCheckResult]


class RegulatoryHealthMonitor:
    """
    Runs registered health checks and aggregates into a ServiceHealth report.

    Checks are lightweight — they inspect in-memory service state and
    never perform I/O.  Each check is timed and results are cached for
    `cache_ttl_s` seconds to avoid thundering-herd during health polling.
    """

    def __init__(self, cache_ttl_s: float = 10.0) -> None:
        self._checks: dict[str, HealthCheckFn] = {}
        self._cache:  ServiceHealth | None  = None
        self._cache_at: float                  = 0.0
        self._cache_ttl = cache_ttl_s

    def register(self, name: str, fn: HealthCheckFn) -> None:
        self._checks[name] = fn

    def check(self, force: bool = False) -> ServiceHealth:
        now = time.monotonic()
        if not force and self._cache and (now - self._cache_at) < self._cache_ttl:
            return self._cache

        results: list[HealthCheckResult] = []
        for name, fn in self._checks.items():
            t0 = time.monotonic()
            try:
                result = fn()
            except Exception as exc:
                result = HealthCheckResult(
                    name       = name,
                    status     = HealthStatus.UNHEALTHY,
                    message    = f"Check raised: {exc}",
                    latency_ms = (time.monotonic() - t0) * 1000.0,
                )
            result.latency_ms = (time.monotonic() - t0) * 1000.0
            results.append(result)

        if any(r.status == HealthStatus.UNHEALTHY for r in results):
            overall = HealthStatus.UNHEALTHY
        elif any(r.status == HealthStatus.DEGRADED for r in results):
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY

        health = ServiceHealth(overall=overall, checks=results)
        self._cache    = health
        self._cache_at = time.monotonic()
        return health

    # ── Built-in checks ───────────────────────────────────────────────────────

    @staticmethod
    def make_ingestion_check(pipeline_getter: Callable) -> HealthCheckFn:
        """Check that the ingestion pipeline singleton is initialised."""
        def _check() -> HealthCheckResult:
            try:
                svc = pipeline_getter()
                doc_count = len(svc._documents)
                return HealthCheckResult(
                    name    = "ingestion_pipeline",
                    status  = HealthStatus.HEALTHY,
                    message = f"{doc_count} document(s) in registry",
                )
            except Exception as exc:
                return HealthCheckResult(
                    name    = "ingestion_pipeline",
                    status  = HealthStatus.UNHEALTHY,
                    message = str(exc),
                )
        return _check

    @staticmethod
    def make_recommendation_check(rec_getter: Callable) -> HealthCheckFn:
        """Check that the recommendation service singleton is initialised."""
        def _check() -> HealthCheckResult:
            try:
                svc = rec_getter()
                rec_count = len(svc._recs)
                return HealthCheckResult(
                    name    = "recommendation_service",
                    status  = HealthStatus.HEALTHY,
                    message = f"{rec_count} recommendation(s) in store",
                )
            except Exception as exc:
                return HealthCheckResult(
                    name    = "recommendation_service",
                    status  = HealthStatus.UNHEALTHY,
                    message = str(exc),
                )
        return _check

    @staticmethod
    def make_graph_check(graph_getter: Callable) -> HealthCheckFn:
        """Check that the graph service singleton is initialised."""
        def _check() -> HealthCheckResult:
            try:
                svc    = graph_getter()
                nodes  = len(svc._nodes)
                edges  = len(svc._edges)
                return HealthCheckResult(
                    name    = "graph_service",
                    status  = HealthStatus.HEALTHY,
                    message = f"{nodes} node(s), {edges} edge(s) in graph",
                )
            except Exception as exc:
                return HealthCheckResult(
                    name    = "graph_service",
                    status  = HealthStatus.UNHEALTHY,
                    message = str(exc),
                )
        return _check

    @staticmethod
    def make_timeline_check(timeline_getter: Callable) -> HealthCheckFn:
        """Check that the timeline service singleton is initialised."""
        def _check() -> HealthCheckResult:
            try:
                svc        = timeline_getter()
                event_count = len(svc._events)
                return HealthCheckResult(
                    name    = "timeline_service",
                    status  = HealthStatus.HEALTHY,
                    message = f"{event_count} event(s) in timeline",
                )
            except Exception as exc:
                return HealthCheckResult(
                    name    = "timeline_service",
                    status  = HealthStatus.UNHEALTHY,
                    message = str(exc),
                )
        return _check

    @staticmethod
    def make_metrics_check(registry: RegulatoryMetricsRegistry) -> HealthCheckFn:
        """Check ingestion error rate is within acceptable bounds."""
        _ALERT_THRESHOLD = 0.25   # 25% error rate triggers DEGRADED

        def _check() -> HealthCheckResult:
            rate = registry.error_rate()
            if rate >= _ALERT_THRESHOLD:
                return HealthCheckResult(
                    name    = "ingestion_error_rate",
                    status  = HealthStatus.DEGRADED,
                    message = f"Ingestion error rate {rate:.1%} exceeds {_ALERT_THRESHOLD:.0%} threshold",
                )
            return HealthCheckResult(
                name    = "ingestion_error_rate",
                status  = HealthStatus.HEALTHY,
                message = f"Error rate {rate:.1%} within bounds",
            )
        return _check


# ── Pipeline latency context manager ─────────────────────────────────────────

class _Timer:
    """Lightweight context manager that records elapsed time into a Histogram."""

    def __init__(self, histogram: Histogram) -> None:
        self._h  = histogram
        self._t0 = 0.0

    def __enter__(self) -> _Timer:
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *_: Any) -> None:
        elapsed_ms = (time.monotonic() - self._t0) * 1000.0
        try:
            self._h.observe(elapsed_ms)
        except Exception:
            pass


def time_ingestion(metrics: RegulatoryMetricsRegistry) -> _Timer:
    return _Timer(metrics.ingestion_latency)


def time_diff(metrics: RegulatoryMetricsRegistry) -> _Timer:
    return _Timer(metrics.diff_latency)


def time_impact(metrics: RegulatoryMetricsRegistry) -> _Timer:
    return _Timer(metrics.impact_latency)


def time_graph_query(metrics: RegulatoryMetricsRegistry) -> _Timer:
    return _Timer(metrics.graph_query_latency)


# ── Singleton ─────────────────────────────────────────────────────────────────

_metrics: RegulatoryMetricsRegistry | None = None
_monitor: RegulatoryHealthMonitor | None   = None


def get_metrics() -> RegulatoryMetricsRegistry:
    global _metrics
    if _metrics is None:
        _metrics = RegulatoryMetricsRegistry()
    return _metrics


def get_health_monitor(cache_ttl_s: float = 10.0) -> RegulatoryHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = RegulatoryHealthMonitor(cache_ttl_s=cache_ttl_s)
        _register_default_checks(_monitor)
    return _monitor


def _register_default_checks(monitor: RegulatoryHealthMonitor) -> None:
    """Wire up all built-in health checks using lazy service getters."""
    from regulatory.graph.service import get_graph_service
    from regulatory.ingestion.pipeline import get_ingestion_pipeline
    from regulatory.recommendations.engine import get_recommendation_service
    from regulatory.timeline.service import get_timeline_service

    metrics = get_metrics()
    monitor.register("ingestion_pipeline",    RegulatoryHealthMonitor.make_ingestion_check(get_ingestion_pipeline))
    monitor.register("recommendation_service",RegulatoryHealthMonitor.make_recommendation_check(get_recommendation_service))
    monitor.register("graph_service",         RegulatoryHealthMonitor.make_graph_check(get_graph_service))
    monitor.register("timeline_service",      RegulatoryHealthMonitor.make_timeline_check(get_timeline_service))
    monitor.register("ingestion_error_rate",  RegulatoryHealthMonitor.make_metrics_check(metrics))
