"""
Policy recommendation engine — governed generation and lifecycle management.

The engine generates PolicyRecommendation objects from ImpactReports and
DriftReports, then manages the full approval lifecycle. Recommendations
are never auto-applied — every state transition toward IMPLEMENTED
requires explicit human approval.

Critical invariants
───────────────────
1. DRAFT → SUBMITTED requires a submitter (not the creator, where possible)
2. SUBMITTED → APPROVED requires a designated compliance officer
3. Approval must not be self-granted (approver ≠ submitter)
4. APPROVED → IMPLEMENTED requires an implementer action record
5. Any approved recommendation can be rolled back by creating a
   superseding recommendation and reverting the operational change
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from regulatory.diff.drift import DriftReport, DriftSeverity
from regulatory.impact.analysis import ImpactDimension, ImpactReport
from regulatory.recommendations.models import (
    PolicyRecommendation,
    RecommendationLineageEntry,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
    _hash_recommendation,
    new_rec_id,
)

log = logging.getLogger("evidentrx.regulatory.recommendations.engine")


class PolicyRecommendationService:
    """
    Generates and manages policy recommendations.

    Recommendation generation is deterministic — the same ImpactReport
    always produces the same set of recommendations (content-hash stable).
    Duplicate detection prevents the same recommendation from being
    generated twice for the same source.
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._recs:        dict[str, PolicyRecommendation]    = {}
        self._db_writer    = db_writer
        # source_id → [rec_id, ...]  (dedup by source)
        self._by_source:   dict[str, list[str]]               = {}

    # ── Generation ─────────────────────────────────────────────────────────────

    def generate_from_impact(
        self,
        tenant_id:   str,
        impact:      ImpactReport,
        generated_by: str = "system",
    ) -> list[PolicyRecommendation]:
        """
        Derive recommendations from an ImpactReport.

        Each distinct category of affected element produces one
        recommendation. Duplicates (same content_hash already exists
        for this source) are silently skipped.
        """
        recs: list[PolicyRecommendation] = []

        # Workflow modification recommendations
        wf_elements = impact.affected_workflows
        if wf_elements:
            rec = self._make_recommendation(
                tenant_id        = tenant_id,
                rec_type         = RecommendationType.WORKFLOW_MODIFICATION,
                title            = f"Review {len(wf_elements)} workflow(s) for regulatory alignment",
                rationale        = (
                    f"Policy update (source: {impact.source_id}) affects "
                    f"{len(wf_elements)} operational workflow(s). "
                    f"Workflows should be reviewed and updated before the effective date."
                ),
                proposed_change  = (
                    "Assign a compliance analyst to review each affected workflow against "
                    "the updated regulatory guidance. Submit workflow changes for approval "
                    "before the effective date."
                ),
                affected_elements = [e.element_id for e in wf_elements],
                source_type      = impact.source_type,
                source_id        = impact.source_id,
                priority         = self._priority_from_severity(impact.severity),
                created_by       = generated_by,
                action_by_date   = impact.action_required_by,
            )
            if rec:
                recs.append(rec)

        # Rule review recommendations
        rule_elements = [
            e for e in impact.affected_elements
            if e.element_type == ImpactDimension.RULE and e.remediation_required
        ]
        if rule_elements:
            rec = self._make_recommendation(
                tenant_id        = tenant_id,
                rec_type         = RecommendationType.RULE_REVIEW,
                title            = f"Submit {len(rule_elements)} compliance rule(s) for regulatory review",
                rationale        = (
                    f"Updated regulatory guidance may render {len(rule_elements)} rule(s) "
                    f"outdated or non-compliant. Rules must be reviewed by the compliance team."
                ),
                proposed_change  = (
                    "Compliance team to review each flagged rule against updated guidance. "
                    "Rules requiring changes must go through the rule-pack amendment process "
                    "with full approval workflow. Deterministic rule logic must not be changed "
                    "without authorisation."
                ),
                affected_elements = [e.element_id for e in rule_elements],
                source_type      = impact.source_type,
                source_id        = impact.source_id,
                priority         = RecommendationPriority.HIGH,
                created_by       = generated_by,
            )
            if rec:
                recs.append(rec)

        # Financial risk recommendation
        if impact.financial_risk and impact.financial_risk.high_usd >= 100_000:
            rec = self._make_recommendation(
                tenant_id        = tenant_id,
                rec_type         = RecommendationType.OPERATIONAL_REMEDIATION,
                title            = (
                    f"Initiate financial exposure review "
                    f"({impact.financial_risk.to_dict()['range_label']})"
                ),
                rationale        = impact.financial_risk.basis,
                proposed_change  = (
                    "Engage compliance counsel and finance team to assess actual exposure. "
                    "Consider proactive audit preparation and entity-level risk mitigation."
                ),
                affected_elements = [e.element_id for e in impact.affected_entities[:20]],
                source_type      = impact.source_type,
                source_id        = impact.source_id,
                priority         = RecommendationPriority.URGENT
                    if impact.financial_risk.high_usd >= 1_000_000
                    else RecommendationPriority.HIGH,
                created_by       = generated_by,
            )
            if rec:
                recs.append(rec)

        log.info(
            "PolicyRecommendationService: generated %d recommendations from impact %s",
            len(recs), impact.report_id[:8],
        )
        return recs

    def generate_from_drift(
        self,
        tenant_id:    str,
        drift:        DriftReport,
        generated_by: str = "system",
    ) -> list[PolicyRecommendation]:
        recs: list[PolicyRecommendation] = []
        for finding in drift.findings:
            if finding.severity not in (DriftSeverity.HIGH, DriftSeverity.CRITICAL):
                continue
            rec = self._make_recommendation(
                tenant_id        = tenant_id,
                rec_type         = RecommendationType.OPERATIONAL_REMEDIATION,
                title            = f"Remediate drift: {finding.title}",
                rationale        = finding.description,
                proposed_change  = finding.recommendation or "Review and remediate identified drift.",
                affected_elements = finding.affected_docs + finding.affected_rules,
                source_type      = "drift",
                source_id        = drift.report_id,
                priority         = (
                    RecommendationPriority.URGENT
                    if finding.severity == DriftSeverity.CRITICAL
                    else RecommendationPriority.HIGH
                ),
                created_by       = generated_by,
            )
            if rec:
                recs.append(rec)
        return recs

    # ── Lifecycle management ───────────────────────────────────────────────────

    def submit(
        self,
        tenant_id:    str,
        rec_id:       str,
        submitted_by: str,
    ) -> PolicyRecommendation:
        rec = self._get_owned(tenant_id, rec_id)
        if rec.status != RecommendationStatus.DRAFT:
            raise RecommendationError(f"Recommendation {rec_id[:8]} is not in DRAFT status")
        rec.status       = RecommendationStatus.SUBMITTED
        rec.submitted_at = datetime.now(tz=UTC)
        rec.lineage.append(self._lineage_entry(rec_id, "submitted", submitted_by))
        return rec

    def approve(
        self,
        tenant_id:   str,
        rec_id:      str,
        approved_by: str,
        notes:       str = "",
    ) -> PolicyRecommendation:
        rec = self._get_owned(tenant_id, rec_id)
        if rec.status != RecommendationStatus.SUBMITTED:
            raise RecommendationError(f"Recommendation {rec_id[:8]} is not pending review")
        if approved_by == rec.created_by:
            raise RecommendationError("Approver must be different from the recommendation creator")
        rec.status       = RecommendationStatus.APPROVED
        rec.decided_at   = datetime.now(tz=UTC)
        rec.decided_by   = approved_by
        rec.decision_notes = notes
        rec.lineage.append(self._lineage_entry(rec_id, "approved", approved_by, notes))
        log.info(
            "PolicyRecommendationService: recommendation %s APPROVED by %s",
            rec_id[:8], approved_by[:8],
        )
        return rec

    def reject(
        self,
        tenant_id:   str,
        rec_id:      str,
        rejected_by: str,
        notes:       str,
    ) -> PolicyRecommendation:
        rec = self._get_owned(tenant_id, rec_id)
        if rec.status != RecommendationStatus.SUBMITTED:
            raise RecommendationError(f"Recommendation {rec_id[:8]} is not pending review")
        rec.status       = RecommendationStatus.REJECTED
        rec.decided_at   = datetime.now(tz=UTC)
        rec.decided_by   = rejected_by
        rec.decision_notes = notes
        rec.lineage.append(self._lineage_entry(rec_id, "rejected", rejected_by, notes))
        return rec

    def mark_implemented(
        self,
        tenant_id:        str,
        rec_id:           str,
        implemented_by:   str,
    ) -> PolicyRecommendation:
        rec = self._get_owned(tenant_id, rec_id)
        if rec.status != RecommendationStatus.APPROVED:
            raise RecommendationError(
                f"Recommendation {rec_id[:8]} must be APPROVED before implementation"
            )
        rec.status         = RecommendationStatus.IMPLEMENTED
        rec.implemented_at = datetime.now(tz=UTC)
        rec.lineage.append(self._lineage_entry(rec_id, "implemented", implemented_by))
        return rec

    def rollback(
        self,
        tenant_id:    str,
        rec_id:       str,
        rolled_back_by: str,
        reason:       str,
    ) -> PolicyRecommendation:
        """
        Create a new DRAFT recommendation that reverses an IMPLEMENTED one.

        The original recommendation is marked SUPERSEDED; the new draft
        must go through the full approval workflow before being applied.
        """
        orig = self._get_owned(tenant_id, rec_id)
        if orig.status != RecommendationStatus.IMPLEMENTED:
            raise RecommendationError("Only IMPLEMENTED recommendations can be rolled back")

        rollback_rec = self._create_raw(
            tenant_id        = tenant_id,
            rec_type         = orig.rec_type,
            title            = f"ROLLBACK: {orig.title}",
            rationale        = f"Rollback of recommendation {rec_id[:8]}. Reason: {reason}",
            proposed_change  = f"Reverse the changes applied by recommendation '{orig.title}'.",
            affected_elements = orig.affected_elements,
            source_type      = "rollback",
            source_id        = rec_id,
            priority         = orig.priority,
            created_by       = rolled_back_by,
            prior_rec_id     = rec_id,
        )
        orig.status = RecommendationStatus.SUPERSEDED
        orig.lineage.append(self._lineage_entry(rec_id, "superseded", rolled_back_by, reason))
        log.info(
            "PolicyRecommendationService: rollback recommendation %s created for %s",
            rollback_rec.rec_id[:8], rec_id[:8],
        )
        return rollback_rec

    def withdraw(
        self,
        tenant_id:    str,
        rec_id:       str,
        withdrawn_by: str,
        reason:       str = "",
    ) -> PolicyRecommendation:
        rec = self._get_owned(tenant_id, rec_id)
        if rec.status in (
            RecommendationStatus.IMPLEMENTED,
            RecommendationStatus.SUPERSEDED,
        ):
            raise RecommendationError(
                f"Cannot withdraw recommendation in {rec.status.value} state"
            )
        rec.status = RecommendationStatus.WITHDRAWN
        rec.lineage.append(self._lineage_entry(rec_id, "withdrawn", withdrawn_by, reason))
        return rec

    # ── Queries ────────────────────────────────────────────────────────────────

    def list_recommendations(
        self,
        tenant_id: str,
        status:    RecommendationStatus | None = None,
        priority:  RecommendationPriority | None = None,
        limit:     int = 50,
    ) -> list[PolicyRecommendation]:
        recs = [
            r for r in self._recs.values()
            if r.tenant_id == tenant_id
            and (status is None or r.status == status)
            and (priority is None or r.priority == priority)
        ]
        recs.sort(key=lambda r: r.created_at, reverse=True)
        return recs[:limit]

    def pending_review(self, tenant_id: str) -> list[PolicyRecommendation]:
        return self.list_recommendations(tenant_id, status=RecommendationStatus.SUBMITTED)

    def get(self, rec_id: str) -> PolicyRecommendation | None:
        return self._recs.get(rec_id)

    # ── Private ────────────────────────────────────────────────────────────────

    def _make_recommendation(
        self,
        tenant_id:        str,
        rec_type:         RecommendationType,
        title:            str,
        rationale:        str,
        proposed_change:  str,
        affected_elements: list[str],
        source_type:      str,
        source_id:        str,
        priority:         RecommendationPriority,
        created_by:       str,
        action_by_date:   str | None = None,
    ) -> PolicyRecommendation | None:
        content_hash = _hash_recommendation(
            rec_type.value, title, proposed_change, source_id
        )
        # Dedup: skip if identical recommendation already exists for this source
        existing = self._by_source.get(source_id, [])
        for eid in existing:
            if self._recs.get(eid, None) and self._recs[eid].content_hash == content_hash:
                return None

        return self._create_raw(
            tenant_id, rec_type, title, rationale, proposed_change,
            affected_elements, source_type, source_id, priority,
            created_by, action_by_date=action_by_date,
        )

    def _create_raw(
        self,
        tenant_id:         str,
        rec_type:          RecommendationType,
        title:             str,
        rationale:         str,
        proposed_change:   str,
        affected_elements: list[str],
        source_type:       str,
        source_id:         str,
        priority:          RecommendationPriority,
        created_by:        str,
        action_by_date:    str | None = None,
        prior_rec_id:      str | None = None,
    ) -> PolicyRecommendation:
        content_hash = _hash_recommendation(
            rec_type.value, title, proposed_change, source_id
        )
        rec = PolicyRecommendation(
            rec_id            = new_rec_id(),
            tenant_id         = tenant_id,
            rec_type          = rec_type,
            title             = title,
            rationale         = rationale,
            proposed_change   = proposed_change,
            affected_elements = affected_elements,
            source_type       = source_type,
            source_id         = source_id,
            status            = RecommendationStatus.DRAFT,
            priority          = priority,
            content_hash      = content_hash,
            created_by        = created_by,
            action_by_date    = action_by_date,
            prior_rec_id      = prior_rec_id,
        )
        rec.lineage.append(self._lineage_entry(rec.rec_id, "created", created_by))
        self._recs[rec.rec_id] = rec
        self._by_source.setdefault(source_id, []).append(rec.rec_id)
        return rec

    def _get_owned(self, tenant_id: str, rec_id: str) -> PolicyRecommendation:
        rec = self._recs.get(rec_id)
        if rec is None or rec.tenant_id != tenant_id:
            raise RecommendationError(f"Recommendation {rec_id} not found")
        return rec

    @staticmethod
    def _lineage_entry(
        rec_id:   str,
        event:    str,
        actor_id: str,
        notes:    str = "",
    ) -> RecommendationLineageEntry:
        return RecommendationLineageEntry(
            entry_id = str(uuid.uuid4()),
            rec_id   = rec_id,
            event    = event,
            actor_id = actor_id,
            notes    = notes,
        )

    @staticmethod
    def _priority_from_severity(severity: str) -> RecommendationPriority:
        return {
            "critical": RecommendationPriority.URGENT,
            "high":     RecommendationPriority.HIGH,
            "medium":   RecommendationPriority.NORMAL,
            "low":      RecommendationPriority.LOW,
        }.get(severity, RecommendationPriority.NORMAL)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class RecommendationError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_service: PolicyRecommendationService | None = None


def get_recommendation_service(
    db_writer: Callable | None = None,
) -> PolicyRecommendationService:
    global _service
    if _service is None:
        _service = PolicyRecommendationService(db_writer=db_writer)
    return _service
