"""
observability — Production-Grade Monitoring & Tracing

Provides:
  - OpenTelemetry distributed tracing (OTLP export)
  - Prometheus metrics registry (counters, histograms, gauges)
  - Structured JSON logging (ECS-compatible)
  - FastAPI request tracing middleware
  - LangGraph workflow execution tracing
  - Agent latency and token usage metrics
  - DB query performance metrics
"""

from observability.tracing     import tracer, get_tracer, start_span
from observability.metrics     import metrics_registry, MetricsRegistry
from observability.logging     import configure_logging, get_logger

__all__ = [
    "tracer",
    "get_tracer",
    "start_span",
    "metrics_registry",
    "MetricsRegistry",
    "configure_logging",
    "get_logger",
]
