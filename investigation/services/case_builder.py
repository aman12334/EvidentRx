"""
CaseBuilderService — orchestrates the full finding → case pipeline.

Flow per run:
  1. Fetch unclustered findings from audit.audit_findings
  2. Build Cluster objects via the clustering domain layer
  3. For each cluster:
       a. Create InvestigationCase
       b. Insert investigation_case_findings rows
       c. Stamp investigation_case_id on audit_findings (convenience FK)
       d. Record CASE_CREATED timeline event
       e. Take initial risk snapshot
  4. Commit in batches
  5. Return summary stats

Idempotency: findings already linked to a case (investigation_case_id IS NOT NULL)
are excluded from the fetch query, so re-runs are safe.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ingestion.base import bulk_insert
from investigation.domain.clustering import (
    Cluster, ClusterConfig, FindingRow, build_clusters, CATEGORY_LABELS,
)
from investigation.domain.states import CasePriority, CaseStatus, derive_priority
from investigation.services.evidence import EvidenceAggregationService
from investigation.services.timeline import TimelineService

logger = logging.getLogger(__name__)

_timeline = TimelineService()
_evidence = EvidenceAggregationService()

_UNCLUSTERED_FINDINGS_SQL = text("""
    SELECT
        af.finding_id,
        af.covered_entity_id,
        af.finding_type,
        af.severity,
        af.rule_code,
        af.violation_period_start   AS service_date,
        af.financial_exposure,
        af.split_billing_id,
        af.purchase_id,
        af.purchase_date,
        af.dispense_id,
        af.dispense_date,
        af.claim_id,
        af.claim_service_date,
        sb.ndc_11,
        sb.patient_id_hash
    FROM audit.audit_findings af
    LEFT JOIN ops.split_billing sb ON sb.split_billing_id = af.split_billing_id
    WHERE af.investigation_case_id IS NULL
      AND af.status = 'open'
      AND (:batch_id IS NULL OR sb.batch_id = CAST(:batch_id AS uuid))
    ORDER BY af.covered_entity_id, af.finding_type, af.violation_period_start
""")


class CaseBuilderService:
    def __init__(self, commit_every: int = 100):
        self.commit_every = commit_every
        self._case_counter: dict[int, int] = {}

    def run(
        self,
        session: Session,
        batch_id: Optional[str] = None,
        config: Optional[ClusterConfig] = None,
    ) -> dict:
        """
        Full clustering pass. Returns summary stats.
        """
        if config is None:
            config = ClusterConfig()

        # Seed case number counter from existing DB cases
        self._seed_counter(session)

        logger.info("Fetching unclustered findings (batch_id=%s)...", batch_id)
        finding_rows = self._fetch_findings(session, batch_id)
        logger.info("Fetched %d unclustered findings", len(finding_rows))

        if not finding_rows:
            return {"cases_created": 0, "findings_clustered": 0, "clusters": 0}

        clusters = build_clusters(finding_rows, config)
        logger.info(
            "Built %d clusters from %d findings (window=%dd)",
            len(clusters), len(finding_rows), config.window_days,
        )

        stats = {"cases_created": 0, "findings_clustered": 0, "clusters": len(clusters)}

        for i, cluster in enumerate(clusters):
            case_id = self._create_case(session, cluster)
            self._link_findings(session, case_id, cluster.findings)
            self._stamp_findings(session, case_id, [f.finding_id for f in cluster.findings])
            _timeline.record(
                session, case_id, "CASE_CREATED",
                {
                    "finding_count": len(cluster.findings),
                    "finding_type": cluster.finding_type,
                    "ndc_11": cluster.ndc_11,
                    "window_start": cluster.window_start.isoformat(),
                    "window_end": cluster.window_end.isoformat(),
                    "rule_codes": list(set(cluster.rule_codes)),
                },
                actor_id="case_builder", actor_type="system",
            )
            _evidence.take_risk_snapshot(session, case_id, trigger="case_created")

            stats["cases_created"] += 1
            stats["findings_clustered"] += len(cluster.findings)

            if (i + 1) % self.commit_every == 0:
                session.commit()
                logger.info("  committed %d / %d cases", i + 1, len(clusters))

        session.commit()
        logger.info(
            "CaseBuilder complete — %d cases created, %d findings clustered",
            stats["cases_created"], stats["findings_clustered"],
        )
        return stats

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_findings(self, session: Session, batch_id: Optional[str]) -> list[FindingRow]:
        rows = session.execute(
            _UNCLUSTERED_FINDINGS_SQL, {"batch_id": batch_id}
        ).fetchall()

        return [
            FindingRow(
                finding_id=r.finding_id,
                covered_entity_id=r.covered_entity_id,
                finding_type=r.finding_type,
                severity=r.severity,
                rule_code=r.rule_code,
                service_date=r.service_date,
                ndc_11=r.ndc_11,
                financial_exposure=(
                    Decimal(str(r.financial_exposure))
                    if r.financial_exposure is not None else None
                ),
                patient_id_hash=r.patient_id_hash,
                split_billing_id=r.split_billing_id,
                purchase_id=r.purchase_id,
                purchase_date=r.purchase_date,
                dispense_id=r.dispense_id,
                dispense_date=r.dispense_date,
                claim_id=r.claim_id,
                claim_service_date=r.claim_service_date,
            )
            for r in rows
        ]

    def _create_case(self, session: Session, cluster: Cluster) -> UUID:
        case_id = uuid4()
        year = cluster.window_start.year
        case_number = self._next_case_number(year)
        priority = derive_priority(cluster.severities)
        now = datetime.now(timezone.utc)

        session.execute(text("""
            INSERT INTO audit.investigation_cases (
                case_id, case_number, covered_entity_id, case_type,
                status, priority, title, opened_at,
                finding_count, workflow_state
            ) VALUES (
                :case_id, :case_number, :ce_id, :case_type,
                :status, :priority, :title, :opened_at,
                :finding_count, CAST(:workflow_state AS jsonb)
            )
        """), {
            "case_id":       str(case_id),
            "case_number":   case_number,
            "ce_id":         str(cluster.covered_entity_id),
            "case_type":     cluster.finding_type,
            "status":        CaseStatus.OPEN.value,
            "priority":      priority.value,
            "title":         cluster.title,
            "opened_at":     now,
            "finding_count": len(cluster.findings),
            "workflow_state": "{}",
        })

        return case_id

    def _link_findings(
        self,
        session: Session,
        case_id: UUID,
        findings: list[FindingRow],
    ) -> None:
        rows = [
            {
                "id":         str(uuid4()),
                "case_id":    str(case_id),
                "finding_id": str(f.finding_id),
                "is_primary": i == 0,          # first finding = primary
                "added_by":   "case_builder",
            }
            for i, f in enumerate(findings)
        ]
        bulk_insert(session, "audit.investigation_case_findings", rows)

    def _stamp_findings(
        self,
        session: Session,
        case_id: UUID,
        finding_ids: list[UUID],
    ) -> None:
        """Denormalize case_id onto audit_findings for fast single-finding lookups."""
        session.execute(text("""
            UPDATE audit.audit_findings
            SET investigation_case_id = :case_id
            WHERE finding_id = ANY(:ids::uuid[])
        """), {
            "case_id": str(case_id),
            "ids":     [str(fid) for fid in finding_ids],
        })

    def _seed_counter(self, session: Session) -> None:
        """Initialize case number counters from existing DB cases."""
        rows = session.execute(text("""
            SELECT EXTRACT(YEAR FROM opened_at)::int AS yr, COUNT(*) AS cnt
            FROM audit.investigation_cases
            GROUP BY yr
        """)).fetchall()
        for row in rows:
            self._case_counter[row.yr] = row.cnt

    def _next_case_number(self, year: int) -> str:
        self._case_counter[year] = self._case_counter.get(year, 0) + 1
        return f"INV-{year}-{self._case_counter[year]:05d}"
