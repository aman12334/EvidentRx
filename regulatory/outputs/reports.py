"""
Regulatory intelligence output report generators.

Produces structured, human-readable reports from the Phase 13 regulatory
intelligence pipeline for three audiences:

  PolicyChangeSummary
    Detailed report on a specific regulatory document diff — for compliance
    analysts reviewing a new version of an HRSA notice or CMS guidance.

  ExecutiveRegulatoryIntelligence
    High-level summary of the current regulatory posture — for C-suite and
    board-level stakeholders. Focuses on exposure, readiness, and pending
    actions without operational detail.

  ComplianceReadinessAssessment
    Full assessment report combining readiness score, drift findings,
    pending recommendations, and required actions — for the compliance
    team preparing for an audit.

Design constraints
──────────────────
- All reports are derived purely from deterministic pipeline outputs
- No LLM inference is used in report generation
- Reports are point-in-time snapshots; they must be regenerated to reflect
  new information
- Every claim in a report is traceable to a source entity (doc_id, rec_id, etc.)
- Reports include an explicit disclaimer that human review is required
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from regulatory.diff.drift import DriftReport, DriftSeverity
from regulatory.diff.engine import ChangeSeverity, PolicyDiff
from regulatory.impact.analysis import ImpactReport
from regulatory.intelligence.readiness import ComplianceReadinessSnapshot, ReadinessBand
from regulatory.recommendations.models import PolicyRecommendation, RecommendationPriority

log = logging.getLogger("evidentrx.regulatory.outputs.reports")

_DISCLAIMER = (
    "This report is an advisory output of the EvidentRx regulatory intelligence "
    "pipeline. It is generated deterministically from ingested regulatory data and "
    "does NOT constitute legal advice. All material compliance decisions require "
    "human review by a qualified compliance officer before implementation."
)


# ── PolicyChangeSummary ─────────────────────────────────────────────────────────

@dataclass
class PolicyChangeSummary:
    """
    Structured summary of a regulatory document diff for compliance analysts.

    Maps directly to the output of PolicyDiffEngine.diff() and
    ImpactAnalysisService.analyze_diff(), providing a human-readable
    narrative alongside structured change data.
    """
    report_id:        str
    tenant_id:        str
    generated_at:     datetime
    doc_title:        str
    prior_version:    str
    new_version:      str
    diff_id:          str
    overall_severity: str
    change_count:     int
    critical_changes: list[dict[str, Any]]
    high_changes:     list[dict[str, Any]]
    affected_domains: list[str]
    financial_exposure: dict[str, Any] | None
    recommendation_count: int
    recommendations:  list[dict[str, Any]]
    narrative:        str
    action_items:     list[str]
    disclaimer:       str = _DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id":          self.report_id,
            "report_type":        "policy_change_summary",
            "tenant_id":          self.tenant_id,
            "generated_at":       self.generated_at.isoformat(),
            "doc_title":          self.doc_title,
            "prior_version":      self.prior_version,
            "new_version":        self.new_version,
            "diff_id":            self.diff_id,
            "overall_severity":   self.overall_severity,
            "change_count":       self.change_count,
            "critical_changes":   self.critical_changes,
            "high_changes":       self.high_changes,
            "affected_domains":   self.affected_domains,
            "financial_exposure": self.financial_exposure,
            "recommendation_count": self.recommendation_count,
            "recommendations":    self.recommendations,
            "narrative":          self.narrative,
            "action_items":       self.action_items,
            "disclaimer":         self.disclaimer,
        }


# ── ExecutiveRegulatoryIntelligence ─────────────────────────────────────────────

@dataclass
class ExecutiveRegulatoryIntelligence:
    """
    High-level regulatory posture summary for executive stakeholders.

    Intentionally omits operational detail.  Focuses on:
    - Overall compliance posture band
    - Number of pending actions requiring attention
    - Material financial exposure bands
    - Critical outstanding recommendations
    """
    report_id:           str
    tenant_id:           str
    generated_at:        datetime
    reporting_period:    str                 # ISO-8601 date range string
    readiness_band:      str
    readiness_score:     float
    critical_issues:     int
    high_issues:         int
    pending_actions:     int
    urgent_recs:         int
    high_recs:           int
    material_exposures:  list[dict[str, Any]]    # financial risk summaries
    top_risks:           list[str]               # narrative risk bullets (max 5)
    posture_trend:       list[dict[str, Any]]    # [{"period": ..., "band": ...}]
    executive_narrative: str
    disclaimer:          str = _DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id":          self.report_id,
            "report_type":        "executive_regulatory_intelligence",
            "tenant_id":          self.tenant_id,
            "generated_at":       self.generated_at.isoformat(),
            "reporting_period":   self.reporting_period,
            "readiness_band":     self.readiness_band,
            "readiness_score":    round(self.readiness_score, 4),
            "critical_issues":    self.critical_issues,
            "high_issues":        self.high_issues,
            "pending_actions":    self.pending_actions,
            "urgent_recs":        self.urgent_recs,
            "high_recs":          self.high_recs,
            "material_exposures": self.material_exposures,
            "top_risks":          self.top_risks,
            "posture_trend":      self.posture_trend,
            "executive_narrative":self.executive_narrative,
            "disclaimer":         self.disclaimer,
        }


# ── ComplianceReadinessAssessment ───────────────────────────────────────────────

@dataclass
class ComplianceReadinessAssessment:
    """
    Full compliance readiness report for audit preparation.

    Combines the readiness snapshot with drill-down detail on each
    contributing signal, outstanding drift findings, and all pending
    recommendations ranked by priority and action deadline.
    """
    report_id:          str
    tenant_id:          str
    generated_at:       datetime
    assessment_period:  str
    snapshot_id:        str
    readiness_score:    float
    readiness_band:     str
    domains_covered:    list[str]
    domains_missing:    list[str]
    signal_detail:      list[dict[str, Any]]    # ReadinessSignal details
    drift_summary:      dict[str, Any] | None
    pending_recs:       list[dict[str, Any]]    # sorted by priority
    required_actions:   list[dict[str, Any]]    # structured action items
    audit_readiness:    str                     # "ready" | "conditionally_ready" | "not_ready"
    narrative:          str
    disclaimer:         str = _DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id":         self.report_id,
            "report_type":       "compliance_readiness_assessment",
            "tenant_id":         self.tenant_id,
            "generated_at":      self.generated_at.isoformat(),
            "assessment_period": self.assessment_period,
            "snapshot_id":       self.snapshot_id,
            "readiness_score":   round(self.readiness_score, 4),
            "readiness_band":    self.readiness_band,
            "domains_covered":   self.domains_covered,
            "domains_missing":   self.domains_missing,
            "signal_count":      len(self.signal_detail),
            "signal_detail":     self.signal_detail,
            "drift_summary":     self.drift_summary,
            "pending_rec_count": len(self.pending_recs),
            "pending_recs":      self.pending_recs,
            "required_actions":  self.required_actions,
            "audit_readiness":   self.audit_readiness,
            "narrative":         self.narrative,
            "disclaimer":        self.disclaimer,
        }


# ── Report Generator ────────────────────────────────────────────────────────────

class RegulatoryReportGenerator:
    """
    Assembles structured reports from Phase 13 pipeline outputs.

    All generation methods are pure functions over their inputs —
    calling the same method with the same inputs always returns the
    same report content (deterministic, replayable).
    """

    def generate_policy_change_summary(
        self,
        tenant_id:       str,
        diff:            PolicyDiff,
        impact:          ImpactReport | None              = None,
        recommendations: list[PolicyRecommendation] | None = None,
    ) -> PolicyChangeSummary:
        """Generate an analyst-facing report from a PolicyDiff."""
        now = datetime.now(tz=UTC)

        _sev_order = [
            ChangeSeverity.CRITICAL,
            ChangeSeverity.HIGH,
            ChangeSeverity.MEDIUM,
            ChangeSeverity.LOW,
            ChangeSeverity.INFORMATIONAL,
        ]

        critical_changes = [
            c.to_dict() for c in diff.changes
            if c.severity == ChangeSeverity.CRITICAL
        ]
        high_changes = [
            c.to_dict() for c in diff.changes
            if c.severity == ChangeSeverity.HIGH
        ]

        affected_domains: list[str] = []
        financial_exposure = None
        if impact:
            affected_domains = [d.value for d in (impact.affected_domains or [])]
            if impact.financial_risk:
                financial_exposure = impact.financial_risk.to_dict()

        recs = recommendations or []
        action_items = self._derive_change_actions(diff, impact, recs)
        narrative    = self._change_narrative(diff, impact)

        return PolicyChangeSummary(
            report_id          = str(uuid.uuid4()),
            tenant_id          = tenant_id,
            generated_at       = now,
            doc_title          = diff.new_doc_id,   # callers may override via metadata
            prior_version      = diff.prior_version,
            new_version        = diff.new_version,
            diff_id            = diff.diff_id,
            overall_severity   = diff.overall_severity.value,
            change_count       = len(diff.changes),
            critical_changes   = critical_changes,
            high_changes       = high_changes,
            affected_domains   = affected_domains,
            financial_exposure = financial_exposure,
            recommendation_count = len(recs),
            recommendations    = [r.to_dict() for r in recs[:10]],
            narrative          = narrative,
            action_items       = action_items,
        )

    def generate_executive_intelligence(
        self,
        tenant_id:       str,
        snapshot:        ComplianceReadinessSnapshot,
        drift_report:    DriftReport | None               = None,
        recommendations: list[PolicyRecommendation] | None = None,
        posture_trend:   list[dict[str, Any]] | None      = None,
        reporting_period: str                                = "",
    ) -> ExecutiveRegulatoryIntelligence:
        """Generate a C-suite-level regulatory posture summary."""
        now  = datetime.now(tz=UTC)
        recs = recommendations or []

        urgent_recs = sum(1 for r in recs if r.priority == RecommendationPriority.URGENT)
        high_recs   = sum(1 for r in recs if r.priority == RecommendationPriority.HIGH)

        material_exposures: list[dict] = []
        if drift_report:
            for f in drift_report.findings[:5]:
                if f.severity in (DriftSeverity.CRITICAL, DriftSeverity.HIGH):
                    material_exposures.append({
                        "drift_type": f.drift_type.value,
                        "severity":   f.severity.value,
                        "title":      f.title,
                    })

        top_risks = self._top_risk_bullets(snapshot, drift_report, recs)
        narrative  = self._executive_narrative(snapshot, urgent_recs, high_recs)

        period = reporting_period or now.strftime("%Y-%m")

        return ExecutiveRegulatoryIntelligence(
            report_id           = str(uuid.uuid4()),
            tenant_id           = tenant_id,
            generated_at        = now,
            reporting_period    = period,
            readiness_band      = snapshot.band.value,
            readiness_score     = snapshot.score,
            critical_issues     = len(snapshot.critical_signals),
            high_issues         = len(snapshot.high_signals),
            pending_actions     = snapshot.pending_recs,
            urgent_recs         = urgent_recs,
            high_recs           = high_recs,
            material_exposures  = material_exposures,
            top_risks           = top_risks,
            posture_trend       = posture_trend or [],
            executive_narrative = narrative,
        )

    def generate_readiness_assessment(
        self,
        tenant_id:         str,
        snapshot:          ComplianceReadinessSnapshot,
        drift_report:      DriftReport | None               = None,
        recommendations:   list[PolicyRecommendation] | None = None,
        assessment_period: str                                 = "",
    ) -> ComplianceReadinessAssessment:
        """Generate a full audit-preparation readiness assessment."""
        now  = datetime.now(tz=UTC)
        recs = recommendations or []

        _priority_order = {
            RecommendationPriority.URGENT: 0,
            RecommendationPriority.HIGH:   1,
            RecommendationPriority.NORMAL: 2,
            RecommendationPriority.LOW:    3,
        }
        sorted_recs = sorted(recs, key=lambda r: _priority_order.get(r.priority, 9))

        drift_summary = drift_report.to_dict() if drift_report else None

        required_actions = self._required_action_items(snapshot, drift_report, sorted_recs)

        audit_readiness = self._audit_readiness_label(snapshot)

        narrative = self._readiness_narrative(snapshot, drift_report, sorted_recs)

        period = assessment_period or now.strftime("%Y-%m")

        return ComplianceReadinessAssessment(
            report_id          = str(uuid.uuid4()),
            tenant_id          = tenant_id,
            generated_at       = now,
            assessment_period  = period,
            snapshot_id        = snapshot.snapshot_id,
            readiness_score    = snapshot.score,
            readiness_band     = snapshot.band.value,
            domains_covered    = snapshot.domains_covered,
            domains_missing    = snapshot.domains_missing,
            signal_detail      = [s.to_dict() for s in snapshot.signals],
            drift_summary      = drift_summary,
            pending_recs       = [r.to_dict() for r in sorted_recs[:20]],
            required_actions   = required_actions,
            audit_readiness    = audit_readiness,
            narrative          = narrative,
        )

    # ── Private ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _derive_change_actions(
        diff:   PolicyDiff,
        impact: ImpactReport | None,
        recs:   list[PolicyRecommendation],
    ) -> list[str]:
        actions = []
        if diff.overall_severity in (ChangeSeverity.CRITICAL, ChangeSeverity.HIGH):
            actions.append(
                f"Review all {diff.overall_severity.value}-severity changes before the next "
                f"audit cycle."
            )
        if impact and impact.affected_workflows:
            actions.append(
                f"Assign compliance analyst to review {len(impact.affected_workflows)} "
                f"affected workflow(s)."
            )
        if impact and impact.financial_risk:
            actions.append(
                f"Initiate financial exposure review "
                f"({impact.financial_risk.to_dict().get('range_label','')})."
            )
        for rec in recs:
            if rec.action_by_date:
                actions.append(
                    f"Complete '{rec.title}' by {rec.action_by_date} "
                    f"(priority: {rec.priority.value})."
                )
        return actions[:10]

    @staticmethod
    def _change_narrative(
        diff:   PolicyDiff,
        impact: ImpactReport | None,
    ) -> str:
        parts = [
            f"Version {diff.new_version} of this regulatory document introduces "
            f"{len(diff.changes)} change(s) with an overall severity of "
            f"{diff.overall_severity.value.upper()}."
        ]
        if impact:
            parts.append(impact.narrative)
        parts.append("Human review is required before any operational changes.")
        return " ".join(parts)

    @staticmethod
    def _top_risk_bullets(
        snapshot:     ComplianceReadinessSnapshot,
        drift_report: DriftReport | None,
        recs:         list[PolicyRecommendation],
    ) -> list[str]:
        bullets = []
        for sig in snapshot.critical_signals[:3]:
            bullets.append(f"[CRITICAL] {sig.reason[:120]}")
        if drift_report:
            for f in drift_report.findings[:2]:
                if f.severity in (DriftSeverity.CRITICAL, DriftSeverity.HIGH):
                    bullets.append(f"[{f.severity.value.upper()}] {f.title}")
        urgent_recs = [r for r in recs if r.priority == RecommendationPriority.URGENT]
        if urgent_recs:
            bullets.append(
                f"{len(urgent_recs)} URGENT recommendation(s) awaiting approval."
            )
        return bullets[:5]

    @staticmethod
    def _executive_narrative(
        snapshot:    ComplianceReadinessSnapshot,
        urgent_recs: int,
        high_recs:   int,
    ) -> str:
        band_label = snapshot.band.value.upper().replace("_", " ")
        parts = [
            f"The organisation's current 340B compliance posture is {band_label} "
            f"(readiness score: {snapshot.score:.2f})."
        ]
        if snapshot.domains_missing:
            parts.append(
                f"Coverage gaps exist for: {', '.join(snapshot.domains_missing)}."
            )
        if urgent_recs:
            parts.append(f"{urgent_recs} urgent recommendation(s) require immediate action.")
        if high_recs:
            parts.append(f"{high_recs} high-priority recommendation(s) are pending review.")
        parts.append(
            "No compliance decision should be made solely on the basis of this "
            "automated report. Human review is required."
        )
        return " ".join(parts)

    @staticmethod
    def _required_action_items(
        snapshot:     ComplianceReadinessSnapshot,
        drift_report: DriftReport | None,
        recs:         list[PolicyRecommendation],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []

        for domain in snapshot.domains_missing:
            actions.append({
                "action_type":  "ingest_document",
                "priority":     "critical",
                "description":  f"Ingest a current governing document for the '{domain}' domain.",
                "source":       "coverage_gap",
            })

        if drift_report:
            for f in drift_report.findings[:5]:
                if f.severity in (DriftSeverity.CRITICAL, DriftSeverity.HIGH):
                    actions.append({
                        "action_type":  "remediate_drift",
                        "priority":     f.severity.value,
                        "description":  f.recommendation or f.title,
                        "source":       f"drift_finding:{f.finding_id[:8]}",
                    })

        for rec in recs[:5]:
            if rec.priority in (RecommendationPriority.URGENT, RecommendationPriority.HIGH):
                actions.append({
                    "action_type":  "advance_recommendation",
                    "priority":     rec.priority.value,
                    "description":  rec.title,
                    "deadline":     rec.action_by_date,
                    "source":       f"recommendation:{rec.rec_id[:8]}",
                })

        return actions

    @staticmethod
    def _audit_readiness_label(snapshot: ComplianceReadinessSnapshot) -> str:
        if snapshot.band in (ReadinessBand.STRONG, ReadinessBand.ADEQUATE):
            return "ready"
        if snapshot.band == ReadinessBand.AT_RISK:
            return "conditionally_ready"
        return "not_ready"

    @staticmethod
    def _readiness_narrative(
        snapshot:     ComplianceReadinessSnapshot,
        drift_report: DriftReport | None,
        recs:         list[PolicyRecommendation],
    ) -> str:
        parts = [snapshot.summary]
        if drift_report and drift_report.findings:
            parts.append(
                f"The most recent drift scan identified {len(drift_report.findings)} "
                f"finding(s) with an overall severity of "
                f"{drift_report.overall_severity.value.upper()}."
            )
        if recs:
            urgent = sum(1 for r in recs if r.priority == RecommendationPriority.URGENT)
            if urgent:
                parts.append(
                    f"{urgent} urgent recommendation(s) must be actioned before "
                    f"the next audit cycle."
                )
        parts.append(_DISCLAIMER)
        return " ".join(parts)


# ── Singleton ──────────────────────────────────────────────────────────────────

_generator: RegulatoryReportGenerator | None = None


def get_report_generator() -> RegulatoryReportGenerator:
    global _generator
    if _generator is None:
        _generator = RegulatoryReportGenerator()
    return _generator
