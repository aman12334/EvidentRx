"""
Automated regulatory impact analysis.

Given a PolicyDiff or DriftReport, determines which tenants, covered
entities, workflows, and rules are concretely affected and provides
evidence-linked impact estimates.

Impact analysis is deterministic and traceable:
  - every affected element is justified by a specific diff change
  - financial risk estimates are range-bounded, not point estimates
  - confidence scores are propagated from the underlying diff engine
  - no autonomous rule modification — output is advisory only

Example output
──────────────
  "New HRSA guidance affects:
   • 14 covered entities (state: CA, TX, FL)
   • 3 Medicaid carve-in workflows
   • 2 escalation pathways
   • estimated exposure increase: $800K–$1.4M"
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from regulatory.diff.drift import DriftReport, DriftSeverity
from regulatory.diff.engine import ChangeSeverity, PolicyChange, PolicyDiff

log = logging.getLogger("evidentrx.regulatory.impact.analysis")


class ImpactDimension(str, Enum):
    WORKFLOW        = "workflow"
    COVERED_ENTITY  = "covered_entity"
    RULE            = "rule"
    ESCALATION      = "escalation"
    FINANCIAL       = "financial"
    OPERATIONAL     = "operational"


@dataclass
class AffectedElement:
    """One element in the impact radius of a policy change."""
    element_id:   str           # workflow_id, entity_id, rule_code, etc.
    element_type: ImpactDimension
    element_name: str
    impact_reason: str          # why this element is affected
    change_ids:   list[str]     # which PolicyChange.change_ids drove this
    confidence:   float         = 1.0
    remediation_required: bool  = False
    remediation_hint:     str   = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id":           self.element_id,
            "element_type":         self.element_type.value,
            "element_name":         self.element_name,
            "impact_reason":        self.impact_reason,
            "change_ids":           self.change_ids,
            "confidence":           round(self.confidence, 3),
            "remediation_required": self.remediation_required,
            "remediation_hint":     self.remediation_hint,
        }


@dataclass
class FinancialRiskEstimate:
    """
    Bounded financial risk range for a policy change.

    Estimates are heuristic-driven and explicitly labelled as such.
    They are never presented as precise forecasts.
    """
    low_usd:   float
    high_usd:  float
    basis:     str          # reasoning for the estimate
    confidence: float       = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "low_usd":    round(self.low_usd),
            "high_usd":   round(self.high_usd),
            "range_label":f"${self.low_usd/1e6:.1f}M–${self.high_usd/1e6:.1f}M",
            "basis":      self.basis,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class ImpactReport:
    """
    Structured impact analysis for a policy change or drift event.

    Linked to a specific PolicyDiff or DriftReport for traceability.
    All affected elements include the chain of evidence back to
    specific PolicyChange objects.
    """
    report_id:        str
    tenant_id:        str
    source_type:      str        # "diff" | "drift"
    source_id:        str        # diff_id or drift report_id
    analyzed_at:      datetime
    affected_elements: list[AffectedElement]
    financial_risk:   FinancialRiskEstimate | None
    summary:          str
    severity:         str
    temporal_window:  str        # e.g. "effective 2026-07-01" or "immediate"
    action_required_by: str | None  # ISO date if deadline known
    metadata:         dict[str, Any]   = field(default_factory=dict)

    @property
    def affected_workflows(self) -> list[AffectedElement]:
        return [e for e in self.affected_elements if e.element_type == ImpactDimension.WORKFLOW]

    @property
    def affected_entities(self) -> list[AffectedElement]:
        return [e for e in self.affected_elements if e.element_type == ImpactDimension.COVERED_ENTITY]

    @property
    def affected_rules(self) -> list[AffectedElement]:
        return [e for e in self.affected_elements if e.element_type == ImpactDimension.RULE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id":          self.report_id,
            "tenant_id":          self.tenant_id,
            "source_type":        self.source_type,
            "source_id":          self.source_id,
            "analyzed_at":        self.analyzed_at.isoformat(),
            "severity":           self.severity,
            "temporal_window":    self.temporal_window,
            "action_required_by": self.action_required_by,
            "summary":            self.summary,
            "affected_workflow_count":  len(self.affected_workflows),
            "affected_entity_count":    len(self.affected_entities),
            "affected_rule_count":      len(self.affected_rules),
            "financial_risk":           self.financial_risk.to_dict() if self.financial_risk else None,
            "affected_elements":        [e.to_dict() for e in self.affected_elements],
        }


# ── Domain knowledge: operational area → affected element templates ────────────

_AREA_WORKFLOW_MAP: dict[str, list[tuple[str, str]]] = {
    "contract_pharmacy":   [
        ("wf_contract_review", "Contract Pharmacy Eligibility Review"),
        ("wf_duplicate_discount", "Duplicate Discount Detection Workflow"),
    ],
    "medicaid_carve_in":  [
        ("wf_carve_in_screen", "Medicaid Carve-In Screening Workflow"),
        ("wf_managed_care_check", "Managed Care Carve-In Validation"),
    ],
    "medicaid_carve_out": [
        ("wf_carve_out_exclusion", "Medicaid Carve-Out Exclusion Workflow"),
    ],
    "audit_requirements": [
        ("wf_audit_package", "Audit Evidence Package Workflow"),
        ("wf_record_retention", "Record Retention Compliance Workflow"),
    ],
    "pricing_integrity":  [
        ("wf_ceiling_price", "Ceiling Price Validation Workflow"),
        ("wf_overcharge_recovery", "Overcharge Recovery Workflow"),
    ],
    "billing_compliance": [
        ("wf_claim_review", "Claims Compliance Review Workflow"),
        ("wf_invoice_audit", "Invoice Audit Workflow"),
    ],
    "covered_entity_elig":[
        ("wf_entity_cert", "Covered Entity Certification Workflow"),
    ],
}

_AREA_RULE_MAP: dict[str, list[tuple[str, str]]] = {
    "contract_pharmacy":   [("CPE-01", "Contract Pharmacy Eligibility"), ("DDE-01", "Duplicate Discount Exclusion")],
    "medicaid_carve_in":   [("MCI-01", "Medicaid Carve-In Detection"), ("MCI-02", "Fee-For-Service Routing")],
    "medicaid_carve_out":  [("MCO-01", "Carve-Out Exclusion Check")],
    "audit_requirements":  [("AUD-01", "Audit Trail Completeness"), ("RET-01", "Record Retention Compliance")],
    "pricing_integrity":   [("PRI-01", "Ceiling Price Validation"), ("PRI-02", "Overcharge Detection")],
    "billing_compliance":  [("BIL-01", "Billing Accuracy Check")],
}

# Rough per-entity financial exposure heuristic (USD per affected entity)
_SEVERITY_EXPOSURE: dict[str, tuple[float, float]] = {
    "critical": (500_000, 2_000_000),
    "high":     (100_000,   500_000),
    "medium":   (10_000,    100_000),
    "low":      (0,          10_000),
}


class ImpactAnalysisService:
    """
    Analyzes the operational and financial impact of regulatory changes.

    All analysis is evidence-linked and deterministic. The service does NOT
    autonomously modify rules or workflows — it produces advisory ImpactReports
    that require human review before any operational changes are made.
    """

    def __init__(self) -> None:
        self._reports: dict[str, ImpactReport] = {}
        # Registered tenant context: tenant_id → {entities, workflows, rules}
        self._tenant_context: dict[str, dict[str, Any]] = {}

    def register_tenant_context(
        self,
        tenant_id:      str,
        entity_ids:     list[str],
        entity_details: list[dict[str, Any]],  # [{id, name, state, type}, ...]
        workflow_ids:   list[str],
        active_rules:   list[str],
    ) -> None:
        """Register the operational context used for impact scoping."""
        self._tenant_context[tenant_id] = {
            "entity_ids":     entity_ids,
            "entity_details": entity_details,
            "workflow_ids":   workflow_ids,
            "active_rules":   active_rules,
        }

    def analyze_diff(
        self,
        tenant_id: str,
        diff:      PolicyDiff,
    ) -> ImpactReport:
        """Produce an ImpactReport from a PolicyDiff."""
        affected = self._elements_from_changes(tenant_id, diff.changes, diff.diff_id)
        severity = diff.overall_severity.value
        risk     = self._estimate_financial_risk(severity, len(affected))
        window   = self._temporal_window(diff)

        summary = self._narrative(
            diff.prior_version, diff.new_version,
            affected, risk, window,
        )
        report = ImpactReport(
            report_id         = str(uuid.uuid4()),
            tenant_id         = tenant_id,
            source_type       = "diff",
            source_id         = diff.diff_id,
            analyzed_at       = datetime.now(tz=UTC),
            affected_elements = affected,
            financial_risk    = risk,
            summary           = summary,
            severity          = severity,
            temporal_window   = window,
            action_required_by = self._effective_date_from_diff(diff),
        )
        self._reports[report.report_id] = report
        log.info(
            "ImpactAnalysisService: diff impact for tenant %s — "
            "%d elements affected, %s severity",
            tenant_id[:8], len(affected), severity,
        )
        return report

    def analyze_drift(
        self,
        tenant_id: str,
        drift:     DriftReport,
    ) -> ImpactReport:
        """Produce an ImpactReport from a DriftReport."""
        affected: list[AffectedElement] = []
        for finding in drift.findings:
            for rule_id in finding.affected_rules:
                affected.append(AffectedElement(
                    element_id    = rule_id,
                    element_type  = ImpactDimension.RULE,
                    element_name  = f"Rule {rule_id}",
                    impact_reason = finding.title,
                    change_ids    = [finding.finding_id],
                    confidence    = 0.85,
                    remediation_required = finding.severity in (
                        DriftSeverity.HIGH, DriftSeverity.CRITICAL
                    ),
                    remediation_hint = finding.recommendation,
                ))
            for wf_id in finding.affected_workflows:
                affected.append(AffectedElement(
                    element_id    = wf_id,
                    element_type  = ImpactDimension.WORKFLOW,
                    element_name  = f"Workflow {wf_id}",
                    impact_reason = finding.title,
                    change_ids    = [finding.finding_id],
                    confidence    = 0.80,
                ))

        severity = drift.overall_severity.value
        risk     = self._estimate_financial_risk(severity, max(len(affected), 1))
        report   = ImpactReport(
            report_id         = str(uuid.uuid4()),
            tenant_id         = tenant_id,
            source_type       = "drift",
            source_id         = drift.report_id,
            analyzed_at       = datetime.now(tz=UTC),
            affected_elements = affected,
            financial_risk    = risk,
            summary           = drift.summary,
            severity          = severity,
            temporal_window   = "varies by finding",
            action_required_by = None,
        )
        self._reports[report.report_id] = report
        return report

    def get_report(self, report_id: str) -> ImpactReport | None:
        return self._reports.get(report_id)

    def list_reports(
        self,
        tenant_id: str,
        limit:     int = 20,
    ) -> list[ImpactReport]:
        rpts = [r for r in self._reports.values() if r.tenant_id == tenant_id]
        rpts.sort(key=lambda r: r.analyzed_at, reverse=True)
        return rpts[:limit]

    # ── Private helpers ────────────────────────────────────────────────────────

    def _elements_from_changes(
        self,
        tenant_id: str,
        changes:   list[PolicyChange],
        source_id: str,
    ) -> list[AffectedElement]:
        ctx    = self._tenant_context.get(tenant_id, {})
        seen_workflow: set[str]  = set()
        seen_rule:     set[str]  = set()
        elements: list[AffectedElement] = []

        for change in changes:
            for area in change.operational_areas:
                # Workflows
                for wf_id, wf_name in _AREA_WORKFLOW_MAP.get(area, []):
                    if wf_id in seen_workflow:
                        continue
                    # Only include if registered for tenant (or no context registered)
                    if ctx.get("workflow_ids") and wf_id not in ctx["workflow_ids"]:
                        continue
                    seen_workflow.add(wf_id)
                    elements.append(AffectedElement(
                        element_id    = wf_id,
                        element_type  = ImpactDimension.WORKFLOW,
                        element_name  = wf_name,
                        impact_reason = change.description,
                        change_ids    = [change.change_id],
                        confidence    = change.confidence,
                        remediation_required = change.severity in (
                            ChangeSeverity.HIGH, ChangeSeverity.CRITICAL
                        ),
                        remediation_hint = (
                            "Review workflow against updated guidance and submit for approval."
                        ),
                    ))
                # Rules
                for rule_code, rule_name in _AREA_RULE_MAP.get(area, []):
                    if rule_code in seen_rule:
                        continue
                    if ctx.get("active_rules") and rule_code not in ctx["active_rules"]:
                        continue
                    seen_rule.add(rule_code)
                    elements.append(AffectedElement(
                        element_id    = rule_code,
                        element_type  = ImpactDimension.RULE,
                        element_name  = rule_name,
                        impact_reason = change.description,
                        change_ids    = [change.change_id],
                        confidence    = change.confidence,
                        remediation_required = change.severity == ChangeSeverity.CRITICAL,
                    ))

            # Covered entities: tag by operational area
            for detail in ctx.get("entity_details", []):
                entity_areas = detail.get("operational_areas", [])
                if any(a in entity_areas for a in change.operational_areas):
                    elements.append(AffectedElement(
                        element_id    = detail["id"],
                        element_type  = ImpactDimension.COVERED_ENTITY,
                        element_name  = detail.get("name", detail["id"]),
                        impact_reason = change.description,
                        change_ids    = [change.change_id],
                        confidence    = 0.75,
                    ))

        return elements

    @staticmethod
    def _estimate_financial_risk(
        severity:       str,
        element_count:  int,
    ) -> FinancialRiskEstimate | None:
        bounds = _SEVERITY_EXPOSURE.get(severity)
        if not bounds or bounds[1] == 0:
            return None
        lo, hi = bounds
        scale  = max(1, element_count / 5)
        return FinancialRiskEstimate(
            low_usd  = lo * scale,
            high_usd = hi * scale,
            basis    = (
                f"Heuristic estimate based on {severity} severity "
                f"and {element_count} affected operational elements. "
                f"Actual exposure depends on entity volume and claim counts."
            ),
            confidence = 0.40,  # explicitly low — these are order-of-magnitude estimates
        )

    @staticmethod
    def _temporal_window(diff: PolicyDiff) -> str:
        for change in diff.changes:
            for kw in change.keywords:
                if "effective date" in kw or "immediate" in kw:
                    return "immediate"
        return "subject to effective date in guidance"

    @staticmethod
    def _effective_date_from_diff(diff: PolicyDiff) -> str | None:
        for change in diff.changes:
            for kw in change.keywords:
                if "effective date" in kw:
                    return None   # date parsing requires document-specific logic
        return None

    @staticmethod
    def _narrative(
        prior_version: str,
        new_version:   str,
        affected:      list[AffectedElement],
        risk:          FinancialRiskEstimate | None,
        window:        str,
    ) -> str:
        wf_count = sum(1 for e in affected if e.element_type == ImpactDimension.WORKFLOW)
        en_count = sum(1 for e in affected if e.element_type == ImpactDimension.COVERED_ENTITY)
        ru_count = sum(1 for e in affected if e.element_type == ImpactDimension.RULE)
        parts = []
        if wf_count:
            parts.append(f"{wf_count} workflow(s)")
        if en_count:
            parts.append(f"{en_count} covered entit{'y' if en_count == 1 else 'ies'}")
        if ru_count:
            parts.append(f"{ru_count} compliance rule(s)")
        affected_str = ", ".join(parts) or "no registered elements"
        risk_str = (
            f"; estimated exposure {risk.to_dict()['range_label']}"
            if risk else ""
        )
        return (
            f"Policy update v{prior_version}→v{new_version} affects {affected_str}"
            f"{risk_str}. Effective: {window}. "
            f"Human review required before any operational changes."
        )
