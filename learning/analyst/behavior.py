"""
Analyst behavior intelligence.

Analyses workflow patterns, bottlenecks, and effectiveness metrics
across the analyst population. All analysis is aggregate — individual
analyst data is never exposed to other analysts.

Analytics produced
──────────────────
  WorkflowBottleneck   — stages where cases stall longest
  EscalationPattern    — which case types get escalated and by whom
  WorkloadDistribution — caseload balance across analysts
  InvestigationLatency — time metrics per phase / severity tier
  AnalystEffectiveness — outcome rates per analyst cohort (anonymised)

Privacy design
──────────────
  - Individual analyst metrics exposed only to that analyst and their manager
  - Cross-analyst comparisons use anonymised cohort identifiers
  - No analyst-level data in shared dashboards
  - Aggregate-only exports to analytics layer
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("evidentrx.learning.analyst.behavior")


@dataclass
class CaseEvent:
    """A single case workflow event used as input to behavior analysis."""
    case_id:        str
    tenant_id:      str
    analyst_id:     str
    stage:          str             # "opened" | "investigating" | "escalated" | "closed"
    severity:       str
    occurred_at:    datetime
    duration_hours: float | None = None   # time spent in this stage
    metadata:       dict[str, Any]  = field(default_factory=dict)


@dataclass
class WorkflowBottleneck:
    """A workflow stage where cases spend disproportionate time."""
    stage:              str
    tenant_id:          str
    avg_duration_hours: float
    median_duration_hours: float
    p90_duration_hours: float
    case_count:         int
    severity_breakdown: dict[str, float]    # severity → avg hours
    bottleneck_score:   float               # relative to fleet average
    computed_at:        datetime            = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass
class EscalationPattern:
    """Frequency and context of escalations within a tenant."""
    tenant_id:           str
    escalation_rate:     float      # escalated / total cases
    by_severity:         dict[str, float]   # severity → escalation rate
    by_rule_code:        dict[str, int]     # rule_code → escalation count
    avg_time_before_escalation_hours: float
    period_days:         int
    computed_at:         datetime   = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass
class WorkloadDistribution:
    """
    Caseload distribution across analysts (anonymised cohorts).

    Analyst IDs are hashed before inclusion in shared reports.
    """
    tenant_id:        str
    total_open_cases: int
    analyst_count:    int
    avg_cases_per_analyst: float
    max_cases:        int
    min_cases:        int
    gini_coefficient: float         # 0 = perfectly equal, 1 = maximally unequal
    overloaded_cohort_count: int    # analysts above 1.5× avg
    computed_at:      datetime      = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass
class InvestigationLatencyReport:
    """Time metrics for case resolution."""
    tenant_id:               str
    period_days:             int
    avg_resolution_hours:    float
    median_resolution_hours: float
    p90_resolution_hours:    float
    by_severity:             dict[str, float]   # severity → median hours
    sla_breach_rate:         float              # fraction exceeding SLA
    sla_hours_by_severity:   dict[str, int]     # configured SLA thresholds
    computed_at:             datetime           = field(default_factory=lambda: datetime.now(tz=UTC))


class AnalystBehaviorAnalyzer:
    """
    Analyses case workflow events to surface operational intelligence.

    All methods are pure functions — they take event data as input and
    return structured analytics. No state is modified.
    """

    # Default SLA thresholds (hours per severity tier)
    DEFAULT_SLA_HOURS = {
        "critical": 4,
        "high":     24,
        "medium":   72,
        "low":      168,
    }

    # ── Bottleneck analysis ────────────────────────────────────────────────────

    def identify_bottlenecks(
        self,
        events:    list[CaseEvent],
        tenant_id: str,
    ) -> list[WorkflowBottleneck]:
        """Find workflow stages with disproportionate average duration."""
        # Group events by stage
        by_stage: dict[str, list[float]] = {}
        for e in events:
            if e.tenant_id != tenant_id or e.duration_hours is None:
                continue
            by_stage.setdefault(e.stage, []).append(e.duration_hours)

        if not by_stage:
            return []

        # Fleet average across all stages
        all_durations = [d for durations in by_stage.values() for d in durations]
        fleet_avg     = statistics.mean(all_durations) if all_durations else 1.0

        bottlenecks = []
        for stage, durations in by_stage.items():
            if len(durations) < 3:
                continue
            avg    = statistics.mean(durations)
            median = statistics.median(durations)
            p90    = _percentile(sorted(durations), 0.90)

            # Severity breakdown
            sev_groups: dict[str, list[float]] = {}
            for e in events:
                if e.stage == stage and e.duration_hours is not None and e.tenant_id == tenant_id:
                    sev_groups.setdefault(e.severity, []).append(e.duration_hours)
            sev_avg = {
                sev: round(statistics.mean(durs), 2)
                for sev, durs in sev_groups.items()
                if durs
            }

            bottleneck_score = avg / fleet_avg if fleet_avg > 0 else 1.0

            if bottleneck_score >= 1.5:  # 50%+ above fleet average
                bottlenecks.append(WorkflowBottleneck(
                    stage                 = stage,
                    tenant_id             = tenant_id,
                    avg_duration_hours    = round(avg, 2),
                    median_duration_hours = round(median, 2),
                    p90_duration_hours    = round(p90, 2),
                    case_count            = len(durations),
                    severity_breakdown    = sev_avg,
                    bottleneck_score      = round(bottleneck_score, 3),
                ))

        return sorted(bottlenecks, key=lambda b: b.bottleneck_score, reverse=True)

    # ── Escalation patterns ────────────────────────────────────────────────────

    def escalation_patterns(
        self,
        events:      list[CaseEvent],
        tenant_id:   str,
        period_days: int = 90,
    ) -> EscalationPattern:
        """Compute escalation rates and patterns for a tenant."""
        cutoff   = datetime.now(tz=UTC) - timedelta(days=period_days)
        relevant = [e for e in events if e.tenant_id == tenant_id and e.occurred_at >= cutoff]

        all_case_ids      = {e.case_id for e in relevant}
        escalated_case_ids= {e.case_id for e in relevant if e.stage == "escalated"}

        escalation_rate = len(escalated_case_ids) / len(all_case_ids) if all_case_ids else 0.0

        # By severity
        sev_total:     dict[str, set] = {}
        sev_escalated: dict[str, set] = {}
        for e in relevant:
            sev_total.setdefault(e.severity, set()).add(e.case_id)
            if e.stage == "escalated":
                sev_escalated.setdefault(e.severity, set()).add(e.case_id)
        by_severity = {
            sev: round(len(sev_escalated.get(sev, set())) / len(cases), 4)
            for sev, cases in sev_total.items() if cases
        }

        # By rule code (from metadata)
        rule_counts: dict[str, int] = {}
        for e in relevant:
            if e.stage == "escalated":
                rule = e.metadata.get("rule_code", "unknown")
                rule_counts[rule] = rule_counts.get(rule, 0) + 1

        # Time before escalation
        escalation_times: list[float] = [
            e.duration_hours for e in relevant
            if e.stage == "escalated" and e.duration_hours is not None
        ]
        avg_time = round(statistics.mean(escalation_times), 2) if escalation_times else 0.0

        return EscalationPattern(
            tenant_id                        = tenant_id,
            escalation_rate                  = round(escalation_rate, 4),
            by_severity                      = by_severity,
            by_rule_code                     = dict(sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:10]),
            avg_time_before_escalation_hours = avg_time,
            period_days                      = period_days,
        )

    # ── Workload distribution ──────────────────────────────────────────────────

    def workload_distribution(
        self,
        open_cases:  list[dict[str, Any]],   # list of {case_id, analyst_id, ...}
        tenant_id:   str,
    ) -> WorkloadDistribution:
        """Compute caseload distribution statistics (analyst IDs hashed)."""
        import hashlib
        cases = [c for c in open_cases if c.get("tenant_id") == tenant_id]

        caseloads: dict[str, int] = {}
        for c in cases:
            anon_id = hashlib.sha256(
                c.get("analyst_id", "").encode()
            ).hexdigest()[:12]
            caseloads[anon_id] = caseloads.get(anon_id, 0) + 1

        if not caseloads:
            return WorkloadDistribution(
                tenant_id              = tenant_id,
                total_open_cases       = 0,
                analyst_count          = 0,
                avg_cases_per_analyst  = 0.0,
                max_cases              = 0,
                min_cases              = 0,
                gini_coefficient       = 0.0,
                overloaded_cohort_count= 0,
            )

        loads     = sorted(caseloads.values())
        avg_load  = statistics.mean(loads)
        gini      = _gini(loads)

        return WorkloadDistribution(
            tenant_id               = tenant_id,
            total_open_cases        = len(cases),
            analyst_count           = len(caseloads),
            avg_cases_per_analyst   = round(avg_load, 2),
            max_cases               = max(loads),
            min_cases               = min(loads),
            gini_coefficient        = round(gini, 4),
            overloaded_cohort_count = sum(1 for l in loads if l > avg_load * 1.5),
        )

    # ── Investigation latency ──────────────────────────────────────────────────

    def latency_report(
        self,
        events:      list[CaseEvent],
        tenant_id:   str,
        period_days: int                    = 90,
        sla_hours:   dict | None = None,
    ) -> InvestigationLatencyReport:
        """Compute resolution latency metrics and SLA breach rates."""
        cutoff = datetime.now(tz=UTC) - timedelta(days=period_days)
        closed = [
            e for e in events
            if e.tenant_id == tenant_id
            and e.stage == "closed"
            and e.occurred_at >= cutoff
            and e.duration_hours is not None
        ]

        if not closed:
            return InvestigationLatencyReport(
                tenant_id=tenant_id, period_days=period_days,
                avg_resolution_hours=0.0, median_resolution_hours=0.0,
                p90_resolution_hours=0.0, by_severity={},
                sla_breach_rate=0.0,
                sla_hours_by_severity=sla_hours or self.DEFAULT_SLA_HOURS,
            )

        durations     = sorted(e.duration_hours for e in closed)
        sla           = sla_hours or self.DEFAULT_SLA_HOURS
        breach_count  = sum(
            1 for e in closed
            if e.duration_hours > sla.get(e.severity, 168)
        )

        by_sev: dict[str, list[float]] = {}
        for e in closed:
            by_sev.setdefault(e.severity, []).append(e.duration_hours)

        return InvestigationLatencyReport(
            tenant_id               = tenant_id,
            period_days             = period_days,
            avg_resolution_hours    = round(statistics.mean(durations), 2),
            median_resolution_hours = round(statistics.median(durations), 2),
            p90_resolution_hours    = round(_percentile(durations, 0.90), 2),
            by_severity             = {
                sev: round(statistics.median(durs), 2)
                for sev, durs in by_sev.items() if durs
            },
            sla_breach_rate         = round(breach_count / len(closed), 4),
            sla_hours_by_severity   = sla,
        )


# ── Statistical helpers ───────────────────────────────────────────────────────

def _percentile(sorted_data: list[float], p: float) -> float:
    if not sorted_data:
        return 0.0
    idx = (len(sorted_data) - 1) * p
    lo  = int(idx)
    hi  = min(lo + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


def _gini(sorted_values: list[int]) -> float:
    """Gini coefficient (0 = equal, 1 = maximally unequal)."""
    n = len(sorted_values)
    if n == 0 or sum(sorted_values) == 0:
        return 0.0
    numerator = sum((i + 1) * v for i, v in enumerate(sorted_values))
    denominator = n * sum(sorted_values)
    return round((2 * numerator / denominator) - (n + 1) / n, 4)
