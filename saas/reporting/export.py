"""
Report export — serialise generated reports to downloadable formats.

Supports PDF (placeholder), CSV, and JSON output. The exporter converts
structured report dataclasses into byte payloads that can be streamed
to the client or stored in tenant object storage.

All exports are tenant-scoped — the tenant_id is embedded in every
output to prevent data confusion in multi-report ZIP bundles.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.saas.reporting.export")


class ExportFormat(str, Enum):
    JSON = "json"
    CSV  = "csv"
    PDF  = "pdf"   # placeholder — requires external PDF renderer


@dataclass
class ExportResult:
    """Result of a report export operation."""
    export_id:    str
    tenant_id:    str
    report_id:    str
    format:       ExportFormat
    content:      bytes
    filename:     str
    size_bytes:   int
    exported_at:  datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    mime_type:    str      = "application/octet-stream"

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id":   self.export_id,
            "tenant_id":   self.tenant_id,
            "report_id":   self.report_id,
            "format":      self.format.value,
            "filename":    self.filename,
            "size_bytes":  self.size_bytes,
            "exported_at": self.exported_at.isoformat(),
            "mime_type":   self.mime_type,
        }


_MIME: dict[ExportFormat, str] = {
    ExportFormat.JSON: "application/json",
    ExportFormat.CSV:  "text/csv",
    ExportFormat.PDF:  "application/pdf",
}


class ReportExporter:
    """
    Converts report dataclass instances to exportable byte payloads.

    Usage
    ─────
    exporter = ReportExporter()
    result   = exporter.export(report.to_dict(), report_id, tenant_id, ExportFormat.JSON)
    """

    def export(
        self,
        report_dict: dict[str, Any],
        report_id:   str,
        tenant_id:   str,
        fmt:         ExportFormat,
        filename:    Optional[str] = None,
    ) -> ExportResult:
        import uuid as _uuid

        if fmt == ExportFormat.JSON:
            content, ext = self._to_json(report_dict), "json"
        elif fmt == ExportFormat.CSV:
            content, ext = self._to_csv(report_dict), "csv"
        elif fmt == ExportFormat.PDF:
            content, ext = self._to_pdf_placeholder(report_dict), "pdf"
        else:
            raise ExportError(f"Unsupported export format: {fmt}")

        slug     = filename or f"report_{report_id[:8]}"
        fname    = f"{slug}.{ext}"
        return ExportResult(
            export_id   = str(_uuid.uuid4()),
            tenant_id   = tenant_id,
            report_id   = report_id,
            format      = fmt,
            content     = content,
            filename    = fname,
            size_bytes  = len(content),
            mime_type   = _MIME[fmt],
        )

    # ── Format renderers ───────────────────────────────────────────────────────

    @staticmethod
    def _to_json(data: dict[str, Any]) -> bytes:
        return json.dumps(data, indent=2, default=str).encode("utf-8")

    @staticmethod
    def _to_csv(data: dict[str, Any]) -> bytes:
        """
        Flatten a report dict to CSV.

        Scalar top-level keys become a single header row. Nested lists
        of dicts (e.g. top_entities_by_volume) are serialised to JSON
        in their cell — callers should prefer JSON for complex reports.
        """
        buf = io.StringIO()
        writer = csv.writer(buf)

        flat: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, (list, dict)):
                flat[k] = json.dumps(v)
            else:
                flat[k] = v

        writer.writerow(list(flat.keys()))
        writer.writerow([str(v) for v in flat.values()])
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def _to_pdf_placeholder(data: dict[str, Any]) -> bytes:
        """
        PDF export requires an external renderer (WeasyPrint, ReportLab, etc.).

        This placeholder encodes the report as JSON and wraps it in a
        comment block so downstream renderers can detect the stub.
        """
        notice = (
            "% PDF_PLACEHOLDER — integrate a PDF renderer to produce real output\n"
            + json.dumps(data, indent=2, default=str)
        )
        return notice.encode("utf-8")

    # ── Bulk export ────────────────────────────────────────────────────────────

    def export_batch(
        self,
        reports:   list[tuple[dict[str, Any], str]],  # (report_dict, report_id)
        tenant_id: str,
        fmt:       ExportFormat,
    ) -> list[ExportResult]:
        """Export multiple reports in the same format."""
        return [
            self.export(rd, rid, tenant_id, fmt)
            for rd, rid in reports
        ]


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ExportError(Exception):
    pass
