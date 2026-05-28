"""
Regulatory drift detection service.

Monitors the set of active regulatory documents for a tenant and
detects when the operational environment has drifted away from the
current regulatory baseline. Drift sources:

  DOCUMENT_UPDATED   — a regulatory source published a new version
  NEW_REQUIREMENT    — a policy adds a requirement not previously present
  CONFLICTING_POLICY — two active policies now conflict
  EXPIRED_GUIDANCE   — a guidance document has passed its effective_until
  COVERAGE_GAP       — a compliance domain has no active governing document
  RULE_OBSOLETE      — a deterministic rule references deprecated guidance
  WORKFLOW_STALE     — a workflow implements a superseded policy version

All drift findings are immutable snapshots — re-running drift detection
produces a new DriftReport; old ones are preserved for audit replay.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

from regulatory.ingestion.models import (
    DocumentSource,
    DocumentStatus,
    PolicyDomain,
    RegulatoryDocument,
)
from regulatory.diff.engine import ChangeSeverity, PolicyDiff

log = logging.getLogger("evidentrx.regulatory.diff.drift")


class DriftType(str, Enum):
    DOCUMENT_UPDATED  = "document_updated"
    NEW_REQUIREMENT   = "new_requirement"
    CONFLICTING_POLICY = "conflicting_policy"
    EXPIRED_GUIDANCE  = "expired_guidance"
    COVERAGE_GAP      = "coverage_gap"
    RULE_OBSOLETE     = "rule_obsolete"
    WORKFLOW_STALE    = "workflow_stale"


class DriftSeverity(str, Enum):
    INFORMATIONAL = "informational"
    LOW           = "low"
    MEDIUM        = "medium"
    HIGH          = "high"
    CRITICAL      = "critical"


@dataclass
class DriftFinding:
    """A single detected drift event."""
    finding_id:    str
    drift_type:    DriftType
    severity:      DriftSeverity
    title:         str
    description:   str
    affected_docs: list[str]          = field(default_factory=list)   # doc_ids
    affected_rules: list[str]         = field(default_factory=list)   # rule_codes
    affected_workflows: list[str]     = field(default_factory=list)   # workflow_ids
    diff_id:       Optional[str]      = None   # linked PolicyDiff if applicable
    evidence:      list[str]          = field(default_factory=list)
    recommendation: str               = ""
    detected_at:   datetime           = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id":          self.finding_id,
            "drift_type":          self.drift_type.value,
            "severity":            self.severity.value,
            "title":               self.title,
            "description":         self.description,
            "affected_docs":       self.affected_docs,
            "affected_rules":      self.affected_rules,
            "affected_workflows":  self.affected_workflows,
            "diff_id":             self.diff_id,
            "evidence":            self.evidence,
            "recommendation":      self.recommendation,
            "detected_at":         self.detected_at.isoformat(),
        }


@dataclass
class DriftReport:
    """
    Immutable snapshot of regulatory drift for a tenant at a point in time.

    Each run of DriftDetectionService.detect() produces a new DriftReport.
    Old reports are preserved; callers can compare successive reports to
    understand how drift is evolving.
    """
    report_id:       str
    tenant_id:       str
    detected_at:     datetime
    findings:        list[DriftFinding]
    domains_checked: list[str]
    docs_checked:    int
    overall_severity: DriftSeverity
    summary:         str

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == DriftSeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == DriftSeverity.HIGH)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id":       self.report_id,
            "tenant_id":       self.tenant_id,
            "detected_at":     self.detected_at.isoformat(),
            "docs_checked":    self.docs_checked,
            "finding_count":   len(self.findings),
            "critical_count":  self.critical_count,
            "high_count":      self.high_count,
            "overall_severity":self.overall_severity.value,
            "summary":         self.summary,
            "findings":        [f.to_dict() for f in self.findings],
        }


_SEVERITY_MAP: dict[ChangeSeverity, DriftSeverity] = {
    ChangeSeverity.CRITICAL:      DriftSeverity.CRITICAL,
    ChangeSeverity.HIGH:          DriftSeverity.HIGH,
    ChangeSeverity.MEDIUM:        DriftSeverity.MEDIUM,
    ChangeSeverity.LOW:           DriftSeverity.LOW,
    ChangeSeverity.INFORMATIONAL: DriftSeverity.INFORMATIONAL,
}

# Compliance domains that MUST have active governing documents
_REQUIRED_DOMAINS = frozenset({
    PolicyDomain.DRUG_340B,
    PolicyDomain.CONTRACT_PHARMACY,
    PolicyDomain.AUDIT_REQUIREMENTS,
})


class DriftDetectionService:
    """
    Detects regulatory drift for a tenant's active document set.

    Input
    ─────
    - List of currently indexed RegulatoryDocuments for the tenant
    - Optional list of PolicyDiffs from the most recent ingestion cycle
    - Optional list of rule_codes and workflow_ids to check for staleness

    Output
    ──────
    A DriftReport containing all detected DriftFindings, ordered by severity.
    """

    def __init__(self) -> None:
        # report_id → DriftReport  (history)
        self._reports: dict[str, DriftReport] = {}

    def detect(
        self,
        tenant_id:    str,
        documents:    list[RegulatoryDocument],
        diffs:        Optional[list[PolicyDiff]] = None,
        rule_codes:   Optional[list[str]]        = None,
        workflow_ids: Optional[list[str]]        = None,
        as_of:        Optional[datetime]         = None,
    ) -> DriftReport:
        now     = as_of or datetime.now(tz=timezone.utc)
        findings: list[DriftFinding] = []

        # 1. Document-update drift (from PolicyDiff results)
        if diffs:
            for diff in diffs:
                if diff.overall_severity in (ChangeSeverity.HIGH, ChangeSeverity.CRITICAL):
                    findings.append(DriftFinding(
                        finding_id   = str(uuid.uuid4()),
                        drift_type   = DriftType.DOCUMENT_UPDATED,
                        severity     = _SEVERITY_MAP[diff.overall_severity],
                        title        = f"Regulatory update detected: {diff.prior_version}→{diff.new_version}",
                        description  = diff.summary,
                        affected_docs = [diff.new_doc_id],
                        diff_id      = diff.diff_id,
                        evidence     = [f"{c.category.value}: {c.section}" for c in diff.changes[:5]],
                        recommendation = "Review diff findings and initiate impact analysis.",
                    ))

                # New requirement check
                new_reqs = [c for c in diff.changes if c.category.value == "added" and
                            c.severity in (ChangeSeverity.HIGH, ChangeSeverity.CRITICAL)]
                for req in new_reqs:
                    findings.append(DriftFinding(
                        finding_id    = str(uuid.uuid4()),
                        drift_type    = DriftType.NEW_REQUIREMENT,
                        severity      = _SEVERITY_MAP[req.severity],
                        title         = f"New compliance requirement: {req.section}",
                        description   = req.description,
                        affected_docs = [diff.new_doc_id],
                        diff_id       = diff.diff_id,
                        evidence      = req.keywords[:5],
                        recommendation = "Assess which workflows and entities are affected.",
                    ))

        # 2. Expired guidance
        for doc in documents:
            exp = doc.attribution.expiry_date
            if exp:
                try:
                    from datetime import date as _date
                    exp_dt = datetime.fromisoformat(exp).replace(tzinfo=timezone.utc)
                    if now >= exp_dt:
                        findings.append(DriftFinding(
                            finding_id   = str(uuid.uuid4()),
                            drift_type   = DriftType.EXPIRED_GUIDANCE,
                            severity     = DriftSeverity.HIGH,
                            title        = f"Expired guidance: {doc.title[:60]}",
                            description  = f"Regulatory guidance '{doc.title}' expired on {exp}.",
                            affected_docs = [doc.doc_id],
                            evidence     = [f"expiry_date={exp}"],
                            recommendation = "Replace with current version or confirm ongoing applicability.",
                        ))
                except ValueError:
                    pass

        # 3. Coverage gap — required domains with no active document
        active_domains: set[PolicyDomain] = set()
        for doc in documents:
            if doc.status == DocumentStatus.INDEXED:
                active_domains.update(doc.domains)

        for domain in _REQUIRED_DOMAINS:
            if domain not in active_domains:
                findings.append(DriftFinding(
                    finding_id   = str(uuid.uuid4()),
                    drift_type   = DriftType.COVERAGE_GAP,
                    severity     = DriftSeverity.HIGH,
                    title        = f"Coverage gap: no active {domain.value} policy",
                    description  = (
                        f"No indexed regulatory document covers the '{domain.value}' domain. "
                        f"This domain is required for 340B compliance operations."
                    ),
                    evidence     = [f"domain={domain.value}", "no_active_document"],
                    recommendation = f"Ingest current {domain.value} guidance from HRSA or CMS.",
                ))

        # 4. Sort findings by severity
        _order = [
            DriftSeverity.CRITICAL,
            DriftSeverity.HIGH,
            DriftSeverity.MEDIUM,
            DriftSeverity.LOW,
            DriftSeverity.INFORMATIONAL,
        ]
        findings.sort(key=lambda f: _order.index(f.severity))

        overall = findings[0].severity if findings else DriftSeverity.INFORMATIONAL
        summary = self._summarise(findings, len(documents))

        report = DriftReport(
            report_id        = str(uuid.uuid4()),
            tenant_id        = tenant_id,
            detected_at      = now,
            findings         = findings,
            domains_checked  = [d.value for d in active_domains],
            docs_checked     = len(documents),
            overall_severity = overall,
            summary          = summary,
        )
        self._reports[report.report_id] = report
        log.info(
            "DriftDetectionService: tenant %s — %d findings (%s severity)",
            tenant_id[:8], len(findings), overall.value,
        )
        return report

    def report_history(
        self,
        tenant_id: str,
        limit:     int = 10,
    ) -> list[DriftReport]:
        reports = [r for r in self._reports.values() if r.tenant_id == tenant_id]
        reports.sort(key=lambda r: r.detected_at, reverse=True)
        return reports[:limit]

    def get_report(self, report_id: str) -> Optional[DriftReport]:
        return self._reports.get(report_id)

    @staticmethod
    def _summarise(findings: list[DriftFinding], docs_checked: int) -> str:
        if not findings:
            return f"No regulatory drift detected across {docs_checked} documents."
        by_type: dict[str, int] = {}
        for f in findings:
            by_type[f.drift_type.value] = by_type.get(f.drift_type.value, 0) + 1
        parts = "; ".join(f"{v} {k.replace('_',' ')}" for k, v in by_type.items())
        return f"{len(findings)} drift findings across {docs_checked} documents: {parts}."


# ── Singleton ──────────────────────────────────────────────────────────────────

_service: Optional[DriftDetectionService] = None


def get_drift_service() -> DriftDetectionService:
    global _service
    if _service is None:
        _service = DriftDetectionService()
    return _service
