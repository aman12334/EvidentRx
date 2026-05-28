"""
WorkflowMemory — in-process state accumulator for a single graph run.

Provides fast access to intermediate results within a run without
re-querying the DB. Scoped to one InvestigationState lifecycle.
This is NOT conversational memory — it is structured evidence context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkflowMemory:
    """
    Holds ephemeral, run-scoped context that agents pass to each other
    without round-tripping through the DB on every read.
    """
    case_id: str
    run_id: str

    # Accumulated evidence context (written by evidence_aggregation node)
    findings_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    total_exposure: Optional[float] = None
    ndc_list: list[str] = field(default_factory=list)
    temporal_window: dict = field(default_factory=dict)

    # Agent output cache (avoid re-running agents on retry)
    _cache: dict = field(default_factory=dict, repr=False)

    def cache_output(self, agent_name: str, output: dict) -> None:
        self._cache[agent_name] = output

    def get_cached(self, agent_name: str) -> Optional[dict]:
        return self._cache.get(agent_name)

    def is_high_risk(self) -> bool:
        return self.critical_count > 0 or (self.high_count > 2)

    def to_context_dict(self) -> dict:
        """Compact summary for injection into agent prompts."""
        return {
            "findings_count": self.findings_count,
            "critical_findings": self.critical_count,
            "high_findings": self.high_count,
            "total_financial_exposure_usd": self.total_exposure,
            "ndc_count": len(self.ndc_list),
            "temporal_window_days": self._window_days(),
        }

    def _window_days(self) -> Optional[int]:
        from datetime import date
        start = self.temporal_window.get("start")
        end = self.temporal_window.get("end")
        if start and end:
            try:
                d0 = date.fromisoformat(start)
                d1 = date.fromisoformat(end)
                return (d1 - d0).days
            except ValueError:
                pass
        return None
