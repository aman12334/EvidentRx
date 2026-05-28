"""
Compliance readiness assessment service.

Produces a point-in-time readiness snapshot for a tenant by synthesising:
  - Active regulatory document coverage across required domains
  - Outstanding drift findings and their severity distribution
  - Pending policy recommendations and their priority distribution
  - Document freshness (age of current governing documents)
  - Open investigation policy contexts

Readiness is expressed as a score (0.0–1.0) plus a categorical band and
a set of named signals that explain the score.  All scoring is fully
deterministic, explainable, and replayable — the same inputs always
produce the same score.

Design constraints
──────────────────
- Readiness scoring NEVER invokes LLM reasoning
- Scores are advisory only and do not auto-trigger any compliance action
- Every deduction is accompanied by a named signal and a reason string
- Historical snapshots are preserved for trend analysis
- Scores must never be used as a substitute for human compliance review
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

from regulatory.ingestion.models import PolicyDomain, RegulatoryDocument, DocumentStatus
from regulatory.diff.drift       import DriftFinding, DriftSeverity
from regulatory.recommendations.models import (
    PolicyRecommendation,
    RecommendationPriority,
    RecommendationStatus,
)

log = logging.getLogger("evidentrx.regulatory.intelligence.readiness")


# Required domains for 340B operational compliance
_REQUIRED_DOMAINS = frozenset({
    PolicyDomain.DRUG_340B,
    PolicyDomain.CONTRACT_PHARMACY,
    PolicyDomain.AUDIT_REQUIREMENTS,
})

# Max age in days before a governing document is considered stale
_STALE_DOCUMENT_DAYS = 365

# Score deductions (additive penalties, capped at 1.0 total)
_DEDUCTION_MISSING_REQUIRED_DOMAIN = 0.15   # per domain
_DEDUCTION_CRITICAL_DRIFT          = 0.12   # per critical finding
_DEDUCTION_HIGH_DRIFT              = 0.07   # per high finding
_DEDUCTION_URGENT_PENDING_REC      = 0.08   # per urgent unactioned recommendation
_DEDUCTION_HIGH_PENDING_REC        = 0.04   # per high unactioned recommendation
_DEDUCTION_STALE_DOCUMENT          = 0.05   # per stale required-domain document


class ReadinessBand(str, Enum):
    STRONG      = "strong"       # 0.85–1.00
    ADEQUATE    = "adequate"     # 0.70–0.84
    AT_RISK     = "at_risk"      # 0.50–0.69
    DEFICIENT   = "deficient"    # 0.25–0.49
    CRITICAL    = "critical"     # 0.00–0.24


def _band(score: float) -> ReadinessBand:
    if score >= 0.85:
        return ReadinessBand.STRONG
    if score >= 0.70:
        return ReadinessBand.ADEQUATE
    if score >= 0.50:
        return ReadinessBand.AT_RISK
    if score >= 0.25:
        return ReadinessBand.DEFICIENT
    return ReadinessBand.CRITICAL


@dataclass
class ReadinessSignal:
    """A named, explainable contributor to the readiness score."""
    signal_id:    str
    name:         str
    category:     str          # "coverage" | "drift" | "recommendations" | "freshness"
    deduction:    float        # amount subtracted from 1.0 (0.0 = no impact)
    reason:       str
    severity:     str          # "info" | "warning" | "high" | "critical"
    affected_ids: list[str]    = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id":    self.signal_id,
            "name":         self.name,
            "category":     self.category,
            "deduction":    round(self.deduction, 4),
            "reason":       self.reason,
            "severity":     self.severity,
            "affected_ids": self.affected_ids,
        }


@dataclass
class ComplianceReadinessSnapshot:
    """
    Point-in-time compliance readiness assessment for one tenant.

    score: 0.0 (fully non-compliant) → 1.0 (fully compliant)
    band:  categorical summary of score
    signals: ordered list of deductions with explanations
    """
    snapshot_id:      str
    tenant_id:        str
    assessed_at:      datetime
    score:            float                   # 0.0–1.0
    band:             ReadinessBand
    signals:          list[ReadinessSignal]
    domains_covered:  list[str]
    domains_missing:  list[str]
    docs_evaluated:   int
    drift_findings:   int
    pending_recs:     int
    summary:          str
    generated_by:     str                    = "system"
    metadata:         dict[str, Any]         = field(default_factory=dict)

    @property
    def total_deduction(self) -> float:
        return min(1.0, sum(s.deduction for s in self.signals))

    @property
    def critical_signals(self) -> list[ReadinessSignal]:
        return [s for s in self.signals if s.severity == "critical"]

    @property
    def high_signals(self) -> list[ReadinessSignal]:
        return [s for s in self.signals if s.severity == "high"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id":     self.snapshot_id,
            "tenant_id":       self.tenant_id,
            "assessed_at":     self.assessed_at.isoformat(),
            "score":           round(self.score, 4),
            "band":            self.band.value,
            "total_deduction": round(self.total_deduction, 4),
            "domains_covered": self.domains_covered,
            "domains_missing": self.domains_missing,
            "docs_evaluated":  self.docs_evaluated,
            "drift_findings":  self.drift_findings,
            "pending_recs":    self.pending_recs,
            "critical_signals":len(self.critical_signals),
            "high_signals":    len(self.high_signals),
            "summary":         self.summary,
            "signals":         [s.to_dict() for s in self.signals],
        }


class ComplianceReadinessService:
    """
    Assesses compliance readiness for a tenant.

    All scoring logic is deterministic — the same set of documents,
    drift findings, and recommendations always produces the same score.
    No LLM inference is involved.
    """

    def __init__(self) -> None:
        # snapshot_id → ComplianceReadinessSnapshot
        self._snapshots: dict[str, ComplianceReadinessSnapshot] = {}

    def assess(
        self,
        tenant_id:       str,
        documents:       list[RegulatoryDocument],
        drift_findings:  Optional[list[DriftFinding]]         = None,
        recommendations: Optional[list[PolicyRecommendation]] = None,
        as_of:           Optional[datetime]                   = None,
        generated_by:    str                                  = "system",
    ) -> ComplianceReadinessSnapshot:
        """
        Compute a compliance readiness snapshot for the tenant.

        Parameters
        ──────────
        documents       : currently indexed regulatory documents
        drift_findings  : findings from the most recent DriftReport (optional)
        recommendations : pending/approved recommendations (optional)
        as_of           : point-in-time override (defaults to now)
        """
        now      = as_of or datetime.now(tz=timezone.utc)
        signals: list[ReadinessSignal] = []

        # ── 1. Domain coverage ──────────────────────────────────────────────────
        indexed_docs    = [d for d in documents if d.status == DocumentStatus.INDEXED]
        covered_domains: set[PolicyDomain] = set()
        for doc in indexed_docs:
            covered_domains.update(doc.domains)

        missing_required = _REQUIRED_DOMAINS - covered_domains
        for domain in sorted(missing_required, key=lambda d: d.value):
            signals.append(ReadinessSignal(
                signal_id    = str(uuid.uuid4()),
                name         = f"missing_domain_{domain.value}",
                category     = "coverage",
                deduction    = _DEDUCTION_MISSING_REQUIRED_DOMAIN,
                reason       = (
                    f"No indexed regulatory document covers the required "
                    f"'{domain.value}' compliance domain."
                ),
                severity     = "critical",
                affected_ids = [domain.value],
            ))

        # ── 2. Drift findings ───────────────────────────────────────────────────
        findings = drift_findings or []
        critical_findings = [f for f in findings if f.severity == DriftSeverity.CRITICAL]
        high_findings     = [f for f in findings if f.severity == DriftSeverity.HIGH]

        for finding in critical_findings:
            signals.append(ReadinessSignal(
                signal_id    = str(uuid.uuid4()),
                name         = f"critical_drift_{finding.drift_type.value}",
                category     = "drift",
                deduction    = _DEDUCTION_CRITICAL_DRIFT,
                reason       = finding.description,
                severity     = "critical",
                affected_ids = finding.affected_docs + finding.affected_rules,
            ))

        for finding in high_findings:
            signals.append(ReadinessSignal(
                signal_id    = str(uuid.uuid4()),
                name         = f"high_drift_{finding.drift_type.value}",
                category     = "drift",
                deduction    = _DEDUCTION_HIGH_DRIFT,
                reason       = finding.description,
                severity     = "high",
                affected_ids = finding.affected_docs + finding.affected_rules,
            ))

        # ── 3. Pending recommendations ──────────────────────────────────────────
        recs      = recommendations or []
        actionable_statuses = {
            RecommendationStatus.DRAFT,
            RecommendationStatus.SUBMITTED,
            RecommendationStatus.APPROVED,
        }
        pending = [r for r in recs if r.status in actionable_statuses]

        urgent_pending = [r for r in pending if r.priority == RecommendationPriority.URGENT]
        high_pending   = [r for r in pending if r.priority == RecommendationPriority.HIGH]

        if urgent_pending:
            signals.append(ReadinessSignal(
                signal_id    = str(uuid.uuid4()),
                name         = "urgent_recommendations_pending",
                category     = "recommendations",
                deduction    = _DEDUCTION_URGENT_PENDING_REC * len(urgent_pending),
                reason       = (
                    f"{len(urgent_pending)} urgent recommendation(s) await action. "
                    f"Unactioned urgent recommendations indicate unresolved high-priority "
                    f"compliance exposure."
                ),
                severity     = "critical",
                affected_ids = [r.rec_id for r in urgent_pending],
            ))

        if high_pending:
            signals.append(ReadinessSignal(
                signal_id    = str(uuid.uuid4()),
                name         = "high_recommendations_pending",
                category     = "recommendations",
                deduction    = _DEDUCTION_HIGH_PENDING_REC * len(high_pending),
                reason       = (
                    f"{len(high_pending)} high-priority recommendation(s) await action. "
                    f"These should be reviewed before the next audit cycle."
                ),
                severity     = "high",
                affected_ids = [r.rec_id for r in high_pending],
            ))

        # ── 4. Document freshness ───────────────────────────────────────────────
        stale_threshold_secs = _STALE_DOCUMENT_DAYS * 86_400
        for doc in indexed_docs:
            if not doc.domains.intersection(_REQUIRED_DOMAINS):
                continue   # only check required-domain documents for staleness
            age_secs = (now - doc.ingested_at).total_seconds()
            if age_secs > stale_threshold_secs:
                age_days = int(age_secs / 86_400)
                signals.append(ReadinessSignal(
                    signal_id    = str(uuid.uuid4()),
                    name         = f"stale_document_{doc.doc_id[:8]}",
                    category     = "freshness",
                    deduction    = _DEDUCTION_STALE_DOCUMENT,
                    reason       = (
                        f"Document '{doc.title[:60]}' is {age_days} days old "
                        f"(threshold: {_STALE_DOCUMENT_DAYS} days). "
                        f"Consider verifying against the latest published version."
                    ),
                    severity     = "warning" if age_days < 548 else "high",
                    affected_ids = [doc.doc_id],
                ))

        # ── 5. Compute final score ──────────────────────────────────────────────
        total_deduction = min(1.0, sum(s.deduction for s in signals))
        score           = round(max(0.0, 1.0 - total_deduction), 4)
        band            = _band(score)

        # Sort signals by deduction descending (biggest impact first)
        signals.sort(key=lambda s: s.deduction, reverse=True)

        domains_covered = sorted(d.value for d in covered_domains)
        domains_missing = sorted(d.value for d in missing_required)

        summary = self._summarise(score, band, signals, domains_missing)

        snapshot = ComplianceReadinessSnapshot(
            snapshot_id     = str(uuid.uuid4()),
            tenant_id       = tenant_id,
            assessed_at     = now,
            score           = score,
            band            = band,
            signals         = signals,
            domains_covered = domains_covered,
            domains_missing = domains_missing,
            docs_evaluated  = len(indexed_docs),
            drift_findings  = len(findings),
            pending_recs    = len(pending),
            summary         = summary,
            generated_by    = generated_by,
        )
        self._snapshots[snapshot.snapshot_id] = snapshot
        log.info(
            "ComplianceReadinessService: tenant %s assessed — score=%.3f band=%s signals=%d",
            tenant_id[:8], score, band.value, len(signals),
        )
        return snapshot

    def snapshot_history(
        self,
        tenant_id: str,
        limit:     int = 20,
    ) -> list[ComplianceReadinessSnapshot]:
        """Return readiness snapshots for a tenant, newest first."""
        snaps = [s for s in self._snapshots.values() if s.tenant_id == tenant_id]
        snaps.sort(key=lambda s: s.assessed_at, reverse=True)
        return snaps[:limit]

    def score_trend(
        self,
        tenant_id: str,
        periods:   int = 6,
    ) -> list[dict[str, Any]]:
        """
        Return a list of (assessed_at, score, band) tuples for trend charting.

        Callers should not make autonomous compliance decisions based on the
        trend alone; this is a monitoring aid only.
        """
        history = self.snapshot_history(tenant_id, limit=periods)
        return [
            {
                "assessed_at": s.assessed_at.isoformat(),
                "score":       s.score,
                "band":        s.band.value,
            }
            for s in reversed(history)
        ]

    def get_snapshot(self, snapshot_id: str) -> Optional[ComplianceReadinessSnapshot]:
        return self._snapshots.get(snapshot_id)

    # ── Private ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _summarise(
        score:           float,
        band:            ReadinessBand,
        signals:         list[ReadinessSignal],
        domains_missing: list[str],
    ) -> str:
        if band == ReadinessBand.STRONG:
            return (
                f"Compliance posture is STRONG (score {score:.2f}). "
                f"All required regulatory domains are covered and no critical drift detected."
            )
        critical_count = sum(1 for s in signals if s.severity == "critical")
        high_count     = sum(1 for s in signals if s.severity == "high")
        parts = []
        if domains_missing:
            parts.append(f"missing coverage for {', '.join(domains_missing)}")
        if critical_count:
            parts.append(f"{critical_count} critical signal(s)")
        if high_count:
            parts.append(f"{high_count} high signal(s)")
        issues = "; ".join(parts) if parts else "minor gaps identified"
        return (
            f"Compliance posture is {band.value.upper()} (score {score:.2f}). "
            f"Issues: {issues}. Human review required before next audit cycle."
        )


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ReadinessAssessmentError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_service: Optional[ComplianceReadinessService] = None


def get_readiness_service() -> ComplianceReadinessService:
    global _service
    if _service is None:
        _service = ComplianceReadinessService()
    return _service
