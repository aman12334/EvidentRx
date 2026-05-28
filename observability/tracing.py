"""
OpenTelemetry distributed tracing setup.

Instruments:
  - FastAPI HTTP requests (via opentelemetry-instrumentation-fastapi)
  - SQLAlchemy queries (via opentelemetry-instrumentation-sqlalchemy)
  - LangGraph workflow nodes (manual spans via start_span)
  - Agent runs (manual spans with token usage attributes)

Exports to OTLP endpoint (Jaeger, Tempo, Honeycomb, etc.) when configured.
Falls back to console export in development.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)


def setup_tracing(service_name: str = "evidentrx-api") -> None:
    """
    Initialize OpenTelemetry tracing.
    Configures OTLP exporter if OTLP_ENDPOINT is set, otherwise no-op.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        from config.settings import settings

        resource = Resource.create({SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)

        if settings.otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint)
        else:
            exporter = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        log.info("OpenTelemetry tracing initialized (endpoint=%s)", settings.otlp_endpoint)

    except ImportError:
        log.warning("opentelemetry not installed — tracing disabled")


def get_tracer(name: str = "evidentrx"):
    """Return an OpenTelemetry tracer (or a no-op tracer if OTel is unavailable)."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


@contextmanager
def start_span(
    name:       str,
    attributes: dict | None = None,
    tracer_name: str = "evidentrx",
) -> Generator[Any, None, None]:
    """
    Context manager that creates a tracing span.
    Falls back to a no-op if OTel is unavailable.

    Usage:
        with start_span("workflow.case_intake", {"case_id": case_id}) as span:
            span.set_attribute("finding_count", len(findings))
            ...
    """
    try:
        from opentelemetry import trace
        t = trace.get_tracer(tracer_name)
        with t.start_as_current_span(name) as span:
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, str(v) if not isinstance(v, (bool, int, float)) else v)
            yield span
    except ImportError:
        yield _NoOpSpan()


class _NoOpSpan:
    """No-op span for when OTel is unavailable."""
    def set_attribute(self, *_: Any, **__: Any) -> None: pass
    def add_event(self, *_: Any, **__: Any) -> None:     pass
    def set_status(self, *_: Any, **__: Any) -> None:    pass
    def record_exception(self, *_: Any, **__: Any) -> None: pass


class _NoOpTracer:
    """No-op tracer for when OTel is unavailable."""
    def start_as_current_span(self, *_: Any, **__: Any):
        from contextlib import contextmanager
        @contextmanager
        def _noop():
            yield _NoOpSpan()
        return _noop()


tracer = get_tracer()
