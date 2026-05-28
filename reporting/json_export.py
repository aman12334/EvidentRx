"""
JSONExporter — exports a complete investigation case as structured JSON.

The export is designed for:
  - Machine consumption by downstream systems
  - Diffing between investigation runs (evaluation harness)
  - Archive and regulatory filing
  - API responses when a REST layer is added later

All fields are serializable (dates → ISO strings, UUIDs → strings).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from reporting.base import ReportData


def _default_serializer(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class JSONExporter:

    def export(self, data: ReportData) -> dict:
        return {
            "schema_version": "1.0",
            "exported_at":    datetime.utcnow().isoformat() + "Z",
            "case_id":        data.case_id,
            "case":           data.case,
            "risk_snapshot":  data.risk_snapshot,
            "findings": {
                "total":    data.total_findings,
                "critical": data.critical_count,
                "high":     data.high_count,
                "records":  data.findings,
            },
            "timeline":          data.timeline,
            "reasoning_traces":  data.reasoning_traces,
            "agent_runs":        data.agent_runs,
            "narrative":         data.narrative,
            "checkpoints":       data.checkpoints,
        }

    def render(self, data: ReportData, indent: int = 2) -> str:
        return json.dumps(self.export(data), indent=indent, default=_default_serializer)

    def write(self, data: ReportData, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        case_num = data.case.get("case_number", data.case_id)
        path = output_dir / f"{case_num}.json"
        path.write_text(self.render(data), encoding="utf-8")
        return path
