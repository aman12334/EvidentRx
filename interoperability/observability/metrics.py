"""
Interoperability layer Prometheus metrics.

Extends the platform's existing metrics with ingestion-specific counters,
histograms, and gauges. All metrics are prefixed with `evidentrx_interop_`.

Metrics registered
──────────────────
  evidentrx_interop_records_ingested_total    — counter by source/type/tenant
  evidentrx_interop_records_rejected_total    — counter by source/reason/tenant
  evidentrx_interop_records_dlq_total         — counter by source/tenant
  evidentrx_interop_ingestion_duration_seconds— histogram by source/type
  evidentrx_interop_connector_health          — gauge by connector/tenant (0=down,1=healthy)
  evidentrx_interop_dlq_depth                 — gauge by tenant
  evidentrx_interop_dedup_rate                — gauge by source/tenant
  evidentrx_interop_quality_score             — histogram by source/canonical_type
  evidentrx_interop_sync_lag_seconds          — gauge: time since last successful sync
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("evidentrx.interop.observability.metrics")

_REGISTRY_INITIALISED = False
_metrics: dict[str, Any] = {}


def register_metrics() -> None:
    """
    Register all interoperability Prometheus metrics.

    Safe to call multiple times (idempotent). Must be called once at
    application startup before any metrics are incremented.
    """
    global _REGISTRY_INITIALISED
    if _REGISTRY_INITIALISED:
        return

    try:
        from prometheus_client import Counter, Histogram, Gauge

        _metrics["records_ingested"] = Counter(
            "evidentrx_interop_records_ingested_total",
            "Total canonical records successfully ingested",
            ["source_system", "canonical_type", "tenant_id"],
        )

        _metrics["records_rejected"] = Counter(
            "evidentrx_interop_records_rejected_total",
            "Total records rejected by validation or policy",
            ["source_system", "reason", "tenant_id"],
        )

        _metrics["records_dlq"] = Counter(
            "evidentrx_interop_records_dlq_total",
            "Total records routed to dead-letter queue",
            ["source_system", "dlq_reason", "tenant_id"],
        )

        _metrics["ingestion_duration"] = Histogram(
            "evidentrx_interop_ingestion_duration_seconds",
            "End-to-end ingestion latency (fetch → persist)",
            ["source_system", "resource_type"],
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
        )

        _metrics["connector_health"] = Gauge(
            "evidentrx_interop_connector_health",
            "Connector health status (1=healthy, 0=unhealthy)",
            ["connector_id", "vendor", "tenant_id"],
        )

        _metrics["dlq_depth"] = Gauge(
            "evidentrx_interop_dlq_depth",
            "Number of unprocessed dead-letter messages",
            ["tenant_id"],
        )

        _metrics["dedup_rate"] = Gauge(
            "evidentrx_interop_dedup_rate",
            "Fraction of records suppressed as duplicates (0.0-1.0)",
            ["source_system", "tenant_id"],
        )

        _metrics["quality_score"] = Histogram(
            "evidentrx_interop_quality_score",
            "Data quality score per record (0.0-1.0)",
            ["source_system", "canonical_type"],
            buckets=[0.0, 0.1, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0],
        )

        _metrics["sync_lag"] = Gauge(
            "evidentrx_interop_sync_lag_seconds",
            "Seconds since last successful sync per connector",
            ["connector_id", "resource_type", "tenant_id"],
        )

        _REGISTRY_INITIALISED = True
        log.info("Interop metrics registered")

    except ImportError:
        log.warning("prometheus_client not installed — interop metrics disabled")


# ── Public metric helpers ─────────────────────────────────────────────────────

def inc_ingested(
    source_system:  str,
    canonical_type: str,
    tenant_id:      str,
    count:          int = 1,
) -> None:
    """Increment the ingested records counter."""
    m = _metrics.get("records_ingested")
    if m:
        m.labels(
            source_system  = source_system,
            canonical_type = canonical_type,
            tenant_id      = tenant_id,
        ).inc(count)


def inc_rejected(
    source_system: str,
    reason:        str,
    tenant_id:     str,
    count:         int = 1,
) -> None:
    """Increment the rejected records counter."""
    m = _metrics.get("records_rejected")
    if m:
        m.labels(
            source_system = source_system,
            reason        = reason[:50],    # cap label length
            tenant_id     = tenant_id,
        ).inc(count)


def inc_dlq(
    source_system: str,
    dlq_reason:    str,
    tenant_id:     str,
) -> None:
    """Increment the DLQ counter."""
    m = _metrics.get("records_dlq")
    if m:
        m.labels(
            source_system = source_system,
            dlq_reason    = dlq_reason,
            tenant_id     = tenant_id,
        ).inc()


def observe_ingestion_duration(
    source_system:  str,
    resource_type:  str,
    duration_sec:   float,
) -> None:
    """Record ingestion duration in the histogram."""
    m = _metrics.get("ingestion_duration")
    if m:
        m.labels(
            source_system = source_system,
            resource_type = resource_type,
        ).observe(duration_sec)


def set_connector_health(
    connector_id: str,
    vendor:       str,
    tenant_id:    str,
    healthy:      bool,
) -> None:
    """Update connector health gauge."""
    m = _metrics.get("connector_health")
    if m:
        m.labels(
            connector_id = connector_id,
            vendor       = vendor,
            tenant_id    = tenant_id,
        ).set(1.0 if healthy else 0.0)


def set_dlq_depth(tenant_id: str, depth: int) -> None:
    """Update DLQ depth gauge for a tenant."""
    m = _metrics.get("dlq_depth")
    if m:
        m.labels(tenant_id=tenant_id).set(depth)


def set_dedup_rate(source_system: str, tenant_id: str, rate: float) -> None:
    """Update the deduplication rate gauge."""
    m = _metrics.get("dedup_rate")
    if m:
        m.labels(source_system=source_system, tenant_id=tenant_id).set(rate)


def observe_quality_score(
    source_system:  str,
    canonical_type: str,
    score:          float,
) -> None:
    """Record a data quality score observation."""
    m = _metrics.get("quality_score")
    if m:
        m.labels(
            source_system  = source_system,
            canonical_type = canonical_type,
        ).observe(score)


def set_sync_lag(
    connector_id:  str,
    resource_type: str,
    tenant_id:     str,
    lag_seconds:   float,
) -> None:
    """Update the sync lag gauge for a connector + resource type."""
    m = _metrics.get("sync_lag")
    if m:
        m.labels(
            connector_id  = connector_id,
            resource_type = resource_type,
            tenant_id     = tenant_id,
        ).set(lag_seconds)
