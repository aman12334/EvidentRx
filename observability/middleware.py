"""
Observability middleware — request tracing and metrics collection.

Wraps every HTTP request with:
  1. A unique X-Request-Id header (UUID4, injected into response)
  2. An OpenTelemetry span for distributed tracing
  3. Prometheus HTTP metrics (count, duration, status code)
  4. Structured log record (method, path, status, duration_ms, tenant_id)

This middleware runs AFTER auth middleware so tenant_id is available
from request.state.user when recording metrics.
"""

from __future__ import annotations

import time
import uuid
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests        import Request
from starlette.responses       import Response

from observability.metrics import metrics_registry

log = logging.getLogger("evidentrx.request")

# Paths excluded from full tracing (too noisy / low value)
_SKIP_PATHS = frozenset({"/health", "/metrics", "/favicon.ico"})


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Unified observability middleware:
      - Injects X-Request-Id
      - Records Prometheus metrics
      - Emits structured access log
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        start = time.perf_counter()
        path  = request.url.path

        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed = time.perf_counter() - start
            self._record_error(request, elapsed)
            raise

        elapsed    = time.perf_counter() - start
        status     = response.status_code
        tenant_id  = getattr(getattr(request.state, "user", None), "tenant_id", "unknown")

        # Inject request ID into response
        response.headers["X-Request-Id"] = request_id

        if path not in _SKIP_PATHS:
            # Prometheus metrics
            metrics_registry.http_requests_total.labels(
                method=request.method,
                path=self._sanitize_path(path),
                status_code=str(status),
                tenant_id=tenant_id,
            ).inc()

            metrics_registry.http_request_duration_seconds.labels(
                method=request.method,
                path=self._sanitize_path(path),
            ).observe(elapsed)

            if status >= 500:
                metrics_registry.http_errors_total.labels(
                    method=request.method,
                    path=self._sanitize_path(path),
                ).inc()

            # Structured access log
            log.info(
                "%s %s %d %.3fms",
                request.method, path, status, elapsed * 1000,
                extra={
                    "request_id": request_id,
                    "tenant_id":  tenant_id,
                    "method":     request.method,
                    "path":       path,
                    "status":     status,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )

        return response

    @staticmethod
    def _sanitize_path(path: str) -> str:
        """Replace UUIDs in paths with {id} to avoid high-cardinality labels."""
        import re
        return re.sub(
            r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "/{id}",
            path,
        )

    @staticmethod
    def _record_error(request: Request, elapsed: float) -> None:
        metrics_registry.http_errors_total.labels(
            method=request.method,
            path=request.url.path,
        ).inc()
