"""
Prometheus metrics registry for EvidentRx.

Exposes metrics at GET /metrics (Prometheus scrape endpoint).
All metrics are namespaced under "evidentrx_".

Metric categories:
  - HTTP: request counts, latency histograms, error rates
  - Workflow: agent run durations, node execution counts
  - Tokens: input/output/cache tokens per agent type
  - DB: query latency, connection pool utilization
  - Business: cases opened/closed, findings created, escalations

Production: collected by Prometheus, visualized in Grafana.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,  # noqa: F401
        Counter,
        Gauge,
        Histogram,
        Summary,  # noqa: F401
        generate_latest,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    log.warning("prometheus_client not installed — metrics disabled")


def _noop(*args, **kwargs):
    class _Noop:
        def labels(self, **_): return self
        def inc(self, *_): pass
        def observe(self, *_): pass
        def set(self, *_): pass
        def time(self): return __import__("contextlib").nullcontext()
    return _Noop()


class MetricsRegistry:
    """
    Centralized Prometheus metrics registry.
    All metrics are created once at startup and reused throughout the process.
    """

    def __init__(self) -> None:
        if not _PROMETHEUS_AVAILABLE:
            self._setup_noop()
            return
        self._setup_real()

    def _setup_noop(self) -> None:
        """Placeholder metrics when prometheus_client is unavailable."""
        for attr in (
            "http_requests_total", "http_request_duration_seconds",
            "http_errors_total", "agent_runs_total",
            "agent_run_duration_seconds", "agent_tokens_total",
            "workflow_node_duration_seconds", "db_query_duration_seconds",
            "cases_total", "findings_total", "escalations_total",
            "active_investigations", "monitoring_runs_total",
            "rate_limit_hits_total", "audit_events_total",
        ):
            setattr(self, attr, _noop())

    def _setup_real(self) -> None:
        # ── HTTP metrics ──────────────────────────────────────────────────
        self.http_requests_total = Counter(
            "evidentrx_http_requests_total",
            "Total HTTP requests",
            ["method", "path", "status_code", "tenant_id"],
        )
        self.http_request_duration_seconds = Histogram(
            "evidentrx_http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "path"],
            buckets=[.005, .01, .025, .05, .1, .25, .5, 1.0, 2.5, 5.0, 10.0],
        )
        self.http_errors_total = Counter(
            "evidentrx_http_errors_total",
            "Total HTTP 5xx errors",
            ["method", "path"],
        )

        # ── Agent / workflow metrics ──────────────────────────────────────
        self.agent_runs_total = Counter(
            "evidentrx_agent_runs_total",
            "Total agent runs",
            ["agent_type", "status", "tenant_id"],
        )
        self.agent_run_duration_seconds = Histogram(
            "evidentrx_agent_run_duration_seconds",
            "Agent run wall-clock time",
            ["agent_type"],
            buckets=[.1, .5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
        )
        self.agent_tokens_total = Counter(
            "evidentrx_agent_tokens_total",
            "Total LLM tokens consumed",
            ["agent_type", "token_type"],   # token_type: input|output|cache_read
        )
        self.workflow_node_duration_seconds = Histogram(
            "evidentrx_workflow_node_duration_seconds",
            "LangGraph workflow node execution time",
            ["node_name"],
            buckets=[.05, .1, .5, 1.0, 5.0, 15.0, 30.0],
        )

        # ── Database metrics ───────────────────────────────────────────────
        self.db_query_duration_seconds = Histogram(
            "evidentrx_db_query_duration_seconds",
            "Database query execution time",
            ["operation", "table"],
            buckets=[.001, .005, .01, .05, .1, .5, 1.0, 5.0],
        )

        # ── Business metrics ───────────────────────────────────────────────
        self.cases_total = Counter(
            "evidentrx_cases_total",
            "Total investigation cases created",
            ["tenant_id", "priority"],
        )
        self.findings_total = Counter(
            "evidentrx_findings_total",
            "Total compliance findings generated",
            ["rule_code", "severity", "tenant_id"],
        )
        self.escalations_total = Counter(
            "evidentrx_escalations_total",
            "Total case escalations",
            ["tenant_id"],
        )
        self.active_investigations = Gauge(
            "evidentrx_active_investigations",
            "Currently open/investigating cases",
            ["tenant_id"],
        )
        self.monitoring_runs_total = Counter(
            "evidentrx_monitoring_runs_total",
            "Total monitoring pipeline runs",
            ["status"],
        )

        # ── Security metrics ───────────────────────────────────────────────
        self.rate_limit_hits_total = Counter(
            "evidentrx_rate_limit_hits_total",
            "Total requests blocked by rate limiter",
            ["endpoint"],
        )
        self.audit_events_total = Counter(
            "evidentrx_audit_events_total",
            "Total audit events written",
            ["event_type"],
        )

    def generate_output(self) -> tuple[bytes, str]:
        """Return (metrics_bytes, content_type) for the /metrics endpoint."""
        if not _PROMETHEUS_AVAILABLE:
            return b"# prometheus_client not installed\n", "text/plain"
        return generate_latest(), CONTENT_TYPE_LATEST


metrics_registry = MetricsRegistry()
