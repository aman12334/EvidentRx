"""
Structured JSON logging configuration (ECS-compatible).

Outputs JSON log records in Elastic Common Schema (ECS) format for:
  - Centralized log aggregation (ELK, Loki, CloudWatch)
  - Structured search across fields
  - Consistent correlation with trace IDs
  - Compliance-safe log storage

Each log record includes:
  - timestamp (ISO 8601 UTC)
  - log.level
  - message
  - service.name
  - trace.id (if active OTel span)
  - http.request.id (if in request context)
  - tenant_id (if in tenant context)
  - event.dataset

Development: pretty-printed JSON via structlog or standard Python logging.
Production: compact JSON one-record-per-line.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter — outputs one JSON object per log line.
    Compatible with ECS (Elastic Common Schema) v1.
    """

    def __init__(self, service_name: str = "evidentrx") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        # Get active OTel trace ID if available
        trace_id = self._get_trace_id()

        log_record: dict[str, Any] = {
            "@timestamp":       datetime.now(tz=UTC).isoformat(),
            "log.level":        record.levelname.lower(),
            "message":          record.getMessage(),
            "service.name":     self.service_name,
            "log.logger":       record.name,
            "log.origin.file.name": record.filename,
            "log.origin.file.line": record.lineno,
        }

        if trace_id:
            log_record["trace.id"] = trace_id

        # Include structured extras passed via extra={...}
        for key in ("tenant_id", "request_id", "user_id", "case_id", "actor_id"):
            val = getattr(record, key, None)
            if val is not None:
                log_record[key] = val

        # Include exception info
        if record.exc_info:
            log_record["error.type"]    = record.exc_info[0].__name__ if record.exc_info[0] else None
            log_record["error.message"] = str(record.exc_info[1]) if record.exc_info[1] else None
            log_record["error.stack_trace"] = self.formatException(record.exc_info)

        return json.dumps(log_record, default=str)

    @staticmethod
    def _get_trace_id() -> str | None:
        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            ctx  = span.get_span_context()
            if ctx.is_valid:
                return format(ctx.trace_id, "032x")
        except Exception:
            pass
        return None


def configure_logging(
    level:        str = "INFO",
    service_name: str = "evidentrx",
    json_output:  bool = True,
) -> None:
    """
    Configure root logger for the application.
    Call once at startup in api/main.py.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear any existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JSONFormatter(service_name=service_name))
    else:
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))

    root.addHandler(handler)

    # Quiet noisy libraries
    for noisy in ("urllib3", "httpx", "asyncio", "botocore", "boto3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — returns a named logger."""
    return logging.getLogger(name)
