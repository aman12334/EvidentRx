"""
Compliance and executive reporting.

Generates structured reports consumed by tenant admin dashboards,
regulatory submissions, and executive briefings. Reports are always
scoped to a single tenant and optionally narrowed to an org.

Report types
────────────
  COMPLIANCE_SUMMARY    — overall compliance posture for a period
  INVESTIGATION_DETAIL  — full case-level breakdown
  EXECUTIVE_DASHBOARD   — KPI snapshot for leadership
  AUDIT_TRAIL           — regulatory audit evidence package
  EXCEPTION_REPORT      — cases that exceeded thresholds or escalated
  TREND_ANALYSIS        — multi-period trend across key metrics
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.reporting.reports")


class ReportType(str, Enum):
    COMPLIANCE_SUMMARY   = "compliance_summary"
    INVESTIGATION_DETAIL = "investigation_detail"
    EXECUTIVE_DASHBOARD  = "executive_dashboard"
    AUDIT_TRAIL          = "audit_trail"
    EXCEPTION_REPORT     = "exception_report"
    TREND_ANALYSIS       = "trend_analysis"


class ReportStatus(str, Enum):
    QUEUED      = "queued"
    GENERATING  = "generating"
    READY       = "ready"
    FAILED      = "failed"
    EXPIRED     = "expired"


@dataclass
class ReportMetadata:
    """Common metadata block for every generated report."""
    report_id:    str
    tenant_id:    str
    report_type:  ReportType
    title:        str
    period_from:  str           # ISO-8601 date
    period_to:    str           # ISO-8601 date
    org_id:       str | None
    generated_by: str
    generated_at: datetime
    status:       ReportStatus  = ReportStatus.QUEUED
    row_count:    int           = 0
    file_size_kb: int | None = None
    expires_at:   datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id":    self.report_id,
            "tenant_id":    self.tenant_id,
            "report_type":  self.report_type.value,
            "title":        self.title,
            "period_from":  self.period_from,
            "period_to":    self.period_to,
            "org_id":       self.org_id,
            "generated_by": self.generated_by,
            "generated_at": self.generated_at.isoformat(),
            "status":       self.status.value,
            "row_count":    self.row_count,
            "file_size_kb": self.file_size_kb,
        }


@dataclass
class ComplianceReport:
    """
    Compliance posture summary for a billing period.

    Aggregates investigation outcomes, alert volumes, rule-pack coverage,
    and exception rates into an executive-readable summary.
    """
    metadata:            ReportMetadata
    total_investigations: int
    closed_investigations: int
    open_investigations:  int
    true_positive_count:  int
    false_positive_count: int
    escalation_count:     int
    remediation_count:    int
    fp_rate:              float        # false positives / total closed
    alert_volume_by_rule: dict[str, int]   # rule_code → alert count
    top_entities_by_volume: list[dict[str, Any]]  # [{entity_id, name, count}]
    rule_pack_coverage:   list[str]    # rule packs active during period
    exception_rate:       float        # escalations / total investigations

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.metadata.to_dict(),
            "total_investigations":   self.total_investigations,
            "closed_investigations":  self.closed_investigations,
            "open_investigations":    self.open_investigations,
            "true_positive_count":    self.true_positive_count,
            "false_positive_count":   self.false_positive_count,
            "escalation_count":       self.escalation_count,
            "remediation_count":      self.remediation_count,
            "fp_rate":                round(self.fp_rate, 4),
            "exception_rate":         round(self.exception_rate, 4),
            "alert_volume_by_rule":   self.alert_volume_by_rule,
            "top_entities_by_volume": self.top_entities_by_volume,
            "rule_pack_coverage":     self.rule_pack_coverage,
        }


@dataclass
class ExecutiveDashboard:
    """
    KPI snapshot for leadership — designed for monthly briefings.

    All rates are floats in [0.0, 1.0]. Trends are month-over-month
    percentage changes (positive = increase).
    """
    metadata:                   ReportMetadata
    investigations_this_period: int
    investigations_trend_pct:   float | None
    fp_rate_this_period:        float
    fp_rate_trend_pct:          float | None
    avg_resolution_hours:       float
    resolution_trend_pct:       float | None
    open_cases_age_p90_hours:   float
    active_rule_packs:          int
    compliance_score:           float    # 0.0–1.0 composite
    risk_flags:                 list[str]   # human-readable warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.metadata.to_dict(),
            "investigations_this_period":  self.investigations_this_period,
            "investigations_trend_pct":    self.investigations_trend_pct,
            "fp_rate_this_period":         round(self.fp_rate_this_period, 4),
            "fp_rate_trend_pct":           self.fp_rate_trend_pct,
            "avg_resolution_hours":        round(self.avg_resolution_hours, 1),
            "resolution_trend_pct":        self.resolution_trend_pct,
            "open_cases_age_p90_hours":    round(self.open_cases_age_p90_hours, 1),
            "active_rule_packs":           self.active_rule_packs,
            "compliance_score":            round(self.compliance_score, 3),
            "risk_flags":                  self.risk_flags,
        }


class ReportEngine:
    """
    Builds compliance and executive reports from pre-aggregated data.

    The engine does not query the database directly — callers supply
    pre-computed metric dicts. This keeps the engine testable and
    decoupled from the ORM.
    """

    def build_compliance_report(
        self,
        tenant_id:      str,
        org_id:         str | None,
        period_from:    str,
        period_to:      str,
        generated_by:   str,
        metrics:        dict[str, Any],
    ) -> ComplianceReport:
        """
        Build a ComplianceReport from pre-computed metrics dict.

        Expected keys in ``metrics``
        ────────────────────────────
        total_investigations, closed, open,
        true_positives, false_positives, escalations, remediations,
        alert_by_rule (dict), top_entities (list), rule_packs (list)
        """
        total   = metrics.get("total_investigations", 0)
        closed  = metrics.get("closed", 0)
        tp      = metrics.get("true_positives", 0)
        fp      = metrics.get("false_positives", 0)
        esc     = metrics.get("escalations", 0)

        fp_rate  = fp / closed if closed > 0 else 0.0
        exc_rate = esc / total if total > 0 else 0.0

        meta = ReportMetadata(
            report_id    = str(uuid.uuid4()),
            tenant_id    = tenant_id,
            report_type  = ReportType.COMPLIANCE_SUMMARY,
            title        = f"Compliance Summary {period_from} – {period_to}",
            period_from  = period_from,
            period_to    = period_to,
            org_id       = org_id,
            generated_by = generated_by,
            generated_at = datetime.now(tz=UTC),
            status       = ReportStatus.READY,
            row_count    = total,
        )
        return ComplianceReport(
            metadata              = meta,
            total_investigations  = total,
            closed_investigations = closed,
            open_investigations   = metrics.get("open", 0),
            true_positive_count   = tp,
            false_positive_count  = fp,
            escalation_count      = esc,
            remediation_count     = metrics.get("remediations", 0),
            fp_rate               = fp_rate,
            alert_volume_by_rule  = metrics.get("alert_by_rule", {}),
            top_entities_by_volume = metrics.get("top_entities", []),
            rule_pack_coverage    = metrics.get("rule_packs", []),
            exception_rate        = exc_rate,
        )

    def build_executive_dashboard(
        self,
        tenant_id:    str,
        org_id:       str | None,
        period_from:  str,
        period_to:    str,
        generated_by: str,
        current:      dict[str, Any],
        prior:        dict[str, Any] | None = None,
    ) -> ExecutiveDashboard:
        """
        Build an ExecutiveDashboard from current + optional prior period metrics.

        ``current`` and ``prior`` should contain:
          investigations, fp_rate, avg_resolution_hours,
          open_cases_age_p90_hours, active_rule_packs, compliance_score
        """
        def trend(curr_val: float, prior_val: float | None) -> float | None:
            if prior_val is None or prior_val == 0.0:
                return None
            return round((curr_val - prior_val) / prior_val * 100, 2)

        risk_flags: list[str] = []
        fp_rate = current.get("fp_rate", 0.0)
        if fp_rate > 0.30:
            risk_flags.append(f"High false-positive rate: {fp_rate:.1%}")
        score = current.get("compliance_score", 1.0)
        if score < 0.70:
            risk_flags.append(f"Compliance score below threshold: {score:.0%}")
        age_p90 = current.get("open_cases_age_p90_hours", 0.0)
        if age_p90 > 720:  # 30 days
            risk_flags.append(f"Open case age P90 exceeds 30 days: {age_p90:.0f}h")

        prior_inv = prior.get("investigations") if prior else None
        prior_fp  = prior.get("fp_rate") if prior else None
        prior_res = prior.get("avg_resolution_hours") if prior else None

        meta = ReportMetadata(
            report_id    = str(uuid.uuid4()),
            tenant_id    = tenant_id,
            report_type  = ReportType.EXECUTIVE_DASHBOARD,
            title        = f"Executive Dashboard {period_from} – {period_to}",
            period_from  = period_from,
            period_to    = period_to,
            org_id       = org_id,
            generated_by = generated_by,
            generated_at = datetime.now(tz=UTC),
            status       = ReportStatus.READY,
        )
        return ExecutiveDashboard(
            metadata                   = meta,
            investigations_this_period = current.get("investigations", 0),
            investigations_trend_pct   = trend(current.get("investigations", 0), prior_inv),
            fp_rate_this_period        = fp_rate,
            fp_rate_trend_pct          = trend(fp_rate, prior_fp),
            avg_resolution_hours       = current.get("avg_resolution_hours", 0.0),
            resolution_trend_pct       = trend(
                current.get("avg_resolution_hours", 0.0), prior_res
            ),
            open_cases_age_p90_hours   = age_p90,
            active_rule_packs          = current.get("active_rule_packs", 0),
            compliance_score           = score,
            risk_flags                 = risk_flags,
        )

    def make_metadata(
        self,
        tenant_id:    str,
        report_type:  ReportType,
        title:        str,
        period_from:  str,
        period_to:    str,
        generated_by: str,
        org_id:       str | None = None,
    ) -> ReportMetadata:
        return ReportMetadata(
            report_id    = str(uuid.uuid4()),
            tenant_id    = tenant_id,
            report_type  = report_type,
            title        = title,
            period_from  = period_from,
            period_to    = period_to,
            org_id       = org_id,
            generated_by = generated_by,
            generated_at = datetime.now(tz=UTC),
            status       = ReportStatus.QUEUED,
        )
