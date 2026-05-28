"""
Finding Clustering — groups audit findings into investigation case candidates.

Algorithm:
  1. Group by (covered_entity_id, finding_type)
  2. For NDC-sensitive rule categories, further group by ndc_11
  3. Within each group, apply temporal sweep: findings within window_days
     of the cluster's start date are in the same cluster.
  4. Return list of Cluster objects.

Design decisions:
  - Pure Python — no DB access. The service layer passes pre-fetched rows.
  - Temporal window uses the cluster's first finding date, not a rolling window,
    so a dense burst of findings doesn't create an unbounded cluster.
  - Minimum cluster size is configurable (default 1) — every finding gets a case.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

# Rule categories where findings for different NDCs should be separated
# (violation is per-drug, not per-entity)
NDC_GROUPED_CATEGORIES: frozenset[str] = frozenset({
    "duplicate_discount",
    "split_billing",
})

CATEGORY_LABELS: dict[str, str] = {
    "duplicate_discount":             "Duplicate Discount",
    "carve_in_out":                   "Medicaid Carve-Out Violation",
    "contract_pharmacy_eligibility":  "Contract Pharmacy Eligibility",
    "split_billing":                  "Split Billing Mismatch",
    "entity_eligibility":             "Entity Eligibility",
    "data_quality":                   "Data Quality",
}


@dataclass
class FindingRow:
    """Lightweight projection of audit_findings + split_billing for clustering."""
    finding_id: UUID
    covered_entity_id: UUID
    finding_type: str          # = rule_category
    severity: str
    rule_code: str
    service_date: date
    ndc_11: str | None
    financial_exposure: Decimal | None
    patient_id_hash: str | None
    split_billing_id: UUID | None
    purchase_id: UUID | None
    purchase_date: date | None
    dispense_id: UUID | None
    dispense_date: date | None
    claim_id: UUID | None
    claim_service_date: date | None


@dataclass
class Cluster:
    """A set of related findings that should become one investigation case."""
    covered_entity_id: UUID
    finding_type: str
    ndc_11: str | None              # None for CE-level violation types
    findings: list[FindingRow] = field(default_factory=list)

    @property
    def window_start(self) -> date:
        return min(f.service_date for f in self.findings)

    @property
    def window_end(self) -> date:
        return max(f.service_date for f in self.findings)

    @property
    def severities(self) -> list[str]:
        return [f.severity for f in self.findings]

    @property
    def rule_codes(self) -> list[str]:
        return [f.rule_code for f in self.findings]

    @property
    def title(self) -> str:
        label = CATEGORY_LABELS.get(self.finding_type, self.finding_type)
        n = len(self.findings)
        start = self.window_start.isoformat()
        end = self.window_end.isoformat()
        ndc_suffix = f" — NDC {self.ndc_11}" if self.ndc_11 else ""
        return f"{label}: {n} finding{'s' if n != 1 else ''}{ndc_suffix} — {start} to {end}"

    @property
    def total_financial_exposure(self) -> Decimal | None:
        amounts = [f.financial_exposure for f in self.findings if f.financial_exposure]
        return sum(amounts, Decimal("0")) if amounts else None


@dataclass
class ClusterConfig:
    window_days: int = 14        # temporal sweep window
    min_cluster_size: int = 1    # minimum findings to create a case


def build_clusters(
    findings: list[FindingRow],
    config: ClusterConfig | None = None,
) -> list[Cluster]:
    """
    Groups findings into Cluster objects. Pure function — no side effects.
    """
    if config is None:
        config = ClusterConfig()

    # Step 1: group by (ce_id, finding_type)
    primary_groups: dict[tuple, list[FindingRow]] = defaultdict(list)
    for f in findings:
        primary_groups[(f.covered_entity_id, f.finding_type)].append(f)

    clusters: list[Cluster] = []

    for (ce_id, finding_type), group in primary_groups.items():
        group.sort(key=lambda f: f.service_date)

        if finding_type in NDC_GROUPED_CATEGORIES:
            # Step 2: sub-group by ndc_11
            ndc_groups: dict[str | None, list[FindingRow]] = defaultdict(list)
            for f in group:
                ndc_groups[f.ndc_11].append(f)
            for ndc_11, ndc_group in ndc_groups.items():
                temporal = _temporal_sweep(ndc_group, config.window_days)
                for tc in temporal:
                    if len(tc) >= config.min_cluster_size:
                        clusters.append(Cluster(
                            covered_entity_id=ce_id,
                            finding_type=finding_type,
                            ndc_11=ndc_11,
                            findings=tc,
                        ))
        else:
            temporal = _temporal_sweep(group, config.window_days)
            for tc in temporal:
                if len(tc) >= config.min_cluster_size:
                    clusters.append(Cluster(
                        covered_entity_id=ce_id,
                        finding_type=finding_type,
                        ndc_11=None,
                        findings=tc,
                    ))

    return clusters


def _temporal_sweep(
    findings: list[FindingRow],
    window_days: int,
) -> list[list[FindingRow]]:
    """
    Splits a sorted list of findings into temporal clusters.
    A new cluster starts when the gap from the current cluster's
    start date exceeds window_days.
    """
    if not findings:
        return []

    clusters: list[list[FindingRow]] = []
    current: list[FindingRow] = [findings[0]]
    window_start: date = findings[0].service_date

    for f in findings[1:]:
        if (f.service_date - window_start).days <= window_days:
            current.append(f)
        else:
            clusters.append(current)
            current = [f]
            window_start = f.service_date

    if current:
        clusters.append(current)

    return clusters
