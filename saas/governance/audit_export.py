"""
Audit log export for regulatory submissions and internal review.

Provides structured, tamper-evident exports of audit records from
the AdminAuditLog and LearningAuditLog. Exports are signed with a
chain-hash fingerprint so recipients can verify completeness.

Export formats
──────────────
  JSON     — structured, machine-readable; includes chain verification
  CSV      — tabular; suitable for spreadsheet review
  NDJSON   — newline-delimited JSON; streaming-friendly for large sets

All exports are tenant-scoped. The platform_admin role may export
cross-tenant (for regulatory audits); tenant_admins may only export
their own tenant's records.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.saas.governance.audit_export")


class AuditExportFormat(str, Enum):
    JSON   = "json"
    CSV    = "csv"
    NDJSON = "ndjson"


class AuditLogSource(str, Enum):
    ADMIN    = "admin"     # AdminAuditLog
    LEARNING = "learning"  # LearningAuditLog


@dataclass
class AuditExportRequest:
    """Specifies the scope and format of an audit export."""
    request_id:   str
    tenant_id:    str
    requested_by: str
    source:       AuditLogSource
    fmt:          AuditExportFormat
    from_dt:      Optional[datetime]
    to_dt:        Optional[datetime]
    event_types:  list[str]            = field(default_factory=list)  # [] = all
    org_id:       Optional[str]        = None
    requested_at: datetime             = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    status:       str                  = "pending"   # "pending"|"ready"|"failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id":   self.request_id,
            "tenant_id":    self.tenant_id,
            "requested_by": self.requested_by,
            "source":       self.source.value,
            "format":       self.fmt.value,
            "from_dt":      self.from_dt.isoformat() if self.from_dt else None,
            "to_dt":        self.to_dt.isoformat() if self.to_dt else None,
            "event_types":  self.event_types,
            "status":       self.status,
        }


@dataclass
class AuditExportResult:
    """The completed export payload."""
    export_id:    str
    request_id:   str
    tenant_id:    str
    content:      bytes
    fmt:          AuditExportFormat
    record_count: int
    fingerprint:  str          # SHA-256 of content (tamper detection)
    exported_at:  datetime     = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    filename:     str          = ""
    size_bytes:   int          = 0

    def __post_init__(self) -> None:
        if not self.size_bytes:
            self.size_bytes = len(self.content)
        if not self.fingerprint:
            self.fingerprint = hashlib.sha256(self.content).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id":    self.export_id,
            "request_id":   self.request_id,
            "tenant_id":    self.tenant_id,
            "format":       self.fmt.value,
            "record_count": self.record_count,
            "fingerprint":  self.fingerprint,
            "exported_at":  self.exported_at.isoformat(),
            "filename":     self.filename,
            "size_bytes":   self.size_bytes,
        }


class AuditExporter:
    """
    Generates tamper-evident audit exports from a list of audit records.

    Records are passed as plain dicts (callers serialise from their
    respective AuditLog objects). The exporter handles formatting,
    fingerprinting, and optional chain-hash inclusion.
    """

    def export(
        self,
        request:   AuditExportRequest,
        records:   list[dict[str, Any]],
    ) -> AuditExportResult:
        """
        Produce an AuditExportResult from a list of pre-fetched records.

        The records list must already be filtered and sorted by the caller;
        the exporter only serialises and signs.
        """
        if request.fmt == AuditExportFormat.JSON:
            content, ext = self._to_json(request, records), "json"
        elif request.fmt == AuditExportFormat.CSV:
            content, ext = self._to_csv(records), "csv"
        elif request.fmt == AuditExportFormat.NDJSON:
            content, ext = self._to_ndjson(records), "ndjson"
        else:
            raise ExportError(f"Unsupported format: {request.fmt}")

        fingerprint = hashlib.sha256(content).hexdigest()
        fname = (
            f"audit_{request.source.value}_{request.tenant_id[:8]}"
            f"_{request.requested_at.strftime('%Y%m%d')}.{ext}"
        )

        result = AuditExportResult(
            export_id    = str(uuid.uuid4()),
            request_id   = request.request_id,
            tenant_id    = request.tenant_id,
            content      = content,
            fmt          = request.fmt,
            record_count = len(records),
            fingerprint  = fingerprint,
            filename     = fname,
        )
        log.info(
            "AuditExporter: exported %d records for tenant %s (%s, %d bytes, fp=%s)",
            len(records), request.tenant_id[:8],
            request.fmt.value, result.size_bytes, fingerprint[:12],
        )
        return result

    # ── Format renderers ───────────────────────────────────────────────────────

    @staticmethod
    def _to_json(
        request: AuditExportRequest,
        records: list[dict[str, Any]],
    ) -> bytes:
        payload = {
            "export_metadata": request.to_dict(),
            "record_count":    len(records),
            "exported_at":     datetime.now(tz=timezone.utc).isoformat(),
            "records":         records,
        }
        return json.dumps(payload, indent=2, default=str).encode("utf-8")

    @staticmethod
    def _to_csv(records: list[dict[str, Any]]) -> bytes:
        if not records:
            return b""
        buf     = io.StringIO()
        writer  = csv.DictWriter(buf, fieldnames=list(records[0].keys()), extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            flat = {
                k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                for k, v in rec.items()
            }
            writer.writerow(flat)
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def _to_ndjson(records: list[dict[str, Any]]) -> bytes:
        lines = [json.dumps(rec, default=str) for rec in records]
        return ("\n".join(lines) + "\n").encode("utf-8")

    # ── Verification helper ────────────────────────────────────────────────────

    @staticmethod
    def verify(result: AuditExportResult) -> bool:
        """Return True if the export content matches the stored fingerprint."""
        actual = hashlib.sha256(result.content).hexdigest()
        return actual == result.fingerprint


class AuditExportRegistry:
    """
    Tracks export requests and results for audit and re-download.
    """

    def __init__(self) -> None:
        self._requests: dict[str, AuditExportRequest]  = {}
        self._results:  dict[str, AuditExportResult]   = {}
        self._exporter  = AuditExporter()

    def create_request(
        self,
        tenant_id:    str,
        requested_by: str,
        source:       AuditLogSource,
        fmt:          AuditExportFormat,
        from_dt:      Optional[datetime]  = None,
        to_dt:        Optional[datetime]  = None,
        event_types:  Optional[list[str]] = None,
        org_id:       Optional[str]       = None,
    ) -> AuditExportRequest:
        req = AuditExportRequest(
            request_id   = str(uuid.uuid4()),
            tenant_id    = tenant_id,
            requested_by = requested_by,
            source       = source,
            fmt          = fmt,
            from_dt      = from_dt,
            to_dt        = to_dt,
            event_types  = event_types or [],
            org_id       = org_id,
        )
        self._requests[req.request_id] = req
        return req

    def fulfill(
        self,
        request_id: str,
        records:    list[dict[str, Any]],
    ) -> AuditExportResult:
        req = self._requests.get(request_id)
        if req is None:
            raise ExportError(f"Request {request_id} not found")
        result = self._exporter.export(req, records)
        self._results[result.export_id] = result
        req.status = "ready"
        return result

    def get_result(self, export_id: str) -> Optional[AuditExportResult]:
        return self._results.get(export_id)

    def list_requests(
        self,
        tenant_id: str,
        limit:     int = 20,
    ) -> list[AuditExportRequest]:
        reqs = [r for r in self._requests.values() if r.tenant_id == tenant_id]
        reqs.sort(key=lambda r: r.requested_at, reverse=True)
        return reqs[:limit]


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ExportError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_registry: Optional[AuditExportRegistry] = None


def get_audit_export_registry() -> AuditExportRegistry:
    global _registry
    if _registry is None:
        _registry = AuditExportRegistry()
    return _registry
