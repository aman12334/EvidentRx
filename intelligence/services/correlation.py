"""
CorrelationEngine — cross-case pattern detection.

Identifies cases that share common entities (pharmacies, NDCs, providers,
covered entities) and computes correlation strength from finding severity
and overlap depth.  Persists results to audit.cross_case_correlations.

Deterministic: derives all data from confirmed findings and case records.
No LLM involvement.  The graph layer feeds richer traversal; this service
adds DB-backed persistence and structured reporting.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from intelligence.graph.builder import ComplianceGraph, ComplianceGraphBuilder
from intelligence.graph.traversal import GraphTraversalService

logger = logging.getLogger(__name__)

_SEVERITY_WEIGHT = {
    "critical": 4.0,
    "high":     2.0,
    "medium":   1.0,
    "low":      0.5,
}

CORRELATION_TYPES = frozenset([
    "shares_pharmacy",
    "shares_ndc",
    "shares_provider",
    "shares_entity",
    "multi_factor",       # 2+ shared dimensions
])


@dataclass
class CorrelationRecord:
    case_id_a:        str
    case_id_b:        str
    correlation_type: str
    strength:         float          # 0.0 – 1.0
    shared_entities:  dict           # what's shared and counts
    explanation:      str
    monitoring_run_id: str | None = None


@dataclass
class CorrelationReport:
    generated_at:      datetime
    total_cases_scanned: int
    total_correlations:  int
    high_strength_count: int         # strength >= 0.6
    shared_pharmacy_count: int
    shared_ndc_count:      int
    multi_factor_count:    int
    records:               list[CorrelationRecord] = field(default_factory=list)
    recurring_pharmacies:  list[dict] = field(default_factory=list)
    recurring_ndcs:        list[dict] = field(default_factory=list)

    def top_n(self, n: int = 10) -> list[CorrelationRecord]:
        return sorted(self.records, key=lambda r: r.strength, reverse=True)[:n]


class CorrelationEngine:
    """
    Cross-case correlation engine.

    Builds or accepts a ComplianceGraph and derives correlation signals
    from shared pharmacies, NDCs, providers, and covered entities.

    Usage::

        engine = CorrelationEngine()
        report = engine.run(session)
        engine.persist(session, report, monitoring_run_id="...")
    """

    def __init__(self) -> None:
        self._builder   = ComplianceGraphBuilder()
        self._traversal = GraphTraversalService()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run(
        self,
        session: Session,
        graph: ComplianceGraph | None = None,
        min_strength: float = 0.15,
        monitoring_run_id: str | None = None,
    ) -> CorrelationReport:
        """
        Runs full correlation analysis.  If graph is not provided, builds
        one from the DB.  Returns a CorrelationReport.
        """
        if graph is None:
            graph = self._builder.build(session)

        # Raw pairwise correlations from graph edges
        raw: list[CorrelationRecord] = self._extract_from_graph(
            graph, min_strength, monitoring_run_id
        )

        # Enrich with multi-factor flag
        pair_types: dict[tuple, list[str]] = defaultdict(list)
        for r in raw:
            key = _pair_key(r.case_id_a, r.case_id_b)
            pair_types[key].append(r.correlation_type)

        # Merge multi-factor pairs
        merged = self._merge_multifactor(raw, pair_types, monitoring_run_id)

        # Recurring entities from traversal service
        recurring_pharmacies = self._traversal.find_recurring_pharmacies(graph, min_cases=2)
        recurring_ndcs       = self._traversal.find_recurring_ndc_violations(graph, min_cases=2)

        total_cases = len({n for n in graph.nodes if n.startswith("case:")})
        high_strength = [r for r in merged if r.strength >= 0.6]

        report = CorrelationReport(
            generated_at=datetime.utcnow(),
            total_cases_scanned=total_cases,
            total_correlations=len(merged),
            high_strength_count=len(high_strength),
            shared_pharmacy_count=sum(1 for r in merged if r.correlation_type == "shares_pharmacy"),
            shared_ndc_count=sum(1 for r in merged if r.correlation_type == "shares_ndc"),
            multi_factor_count=sum(1 for r in merged if r.correlation_type == "multi_factor"),
            records=sorted(merged, key=lambda r: r.strength, reverse=True),
            recurring_pharmacies=recurring_pharmacies,
            recurring_ndcs=recurring_ndcs,
        )

        logger.info(
            "Correlation analysis complete cases=%d correlations=%d high_strength=%d",
            total_cases, len(merged), len(high_strength),
        )
        return report

    def run_for_case(
        self,
        session: Session,
        case_id: str,
        graph: ComplianceGraph | None = None,
        min_strength: float = 0.15,
    ) -> list[CorrelationRecord]:
        """Returns correlations involving a specific case, ordered by strength."""
        if graph is None:
            graph = self._builder.build(session)

        correlated = self._traversal.find_correlated_cases(
            graph, case_id, min_strength=min_strength
        )

        records = []
        for c in correlated:
            a, b = sorted([case_id, c["case_id"]])
            records.append(CorrelationRecord(
                case_id_a=a,
                case_id_b=b,
                correlation_type=c["relationship"],
                strength=c["strength"],
                shared_entities=c.get("shared", {}),
                explanation=_build_explanation(c["relationship"], c.get("shared", {})),
            ))
        return records

    def persist(
        self,
        session: Session,
        report: CorrelationReport,
        monitoring_run_id: str | None = None,
    ) -> int:
        """
        Upserts CorrelationRecord rows into audit.cross_case_correlations.
        Returns number of rows written.
        """
        count = 0
        run_id = monitoring_run_id
        for r in report.records:
            import json
            session.execute(text("""
                INSERT INTO audit.cross_case_correlations
                    (case_id_a, case_id_b, correlation_type, strength,
                     shared_entities, explanation, detected_at, monitoring_run_id)
                VALUES
                    (:ca::uuid, :cb::uuid, :ct, :strength,
                     :shared::jsonb, :explanation, NOW(), :run_id)
                ON CONFLICT (case_id_a, case_id_b, correlation_type)
                DO UPDATE SET
                    strength        = EXCLUDED.strength,
                    shared_entities = EXCLUDED.shared_entities,
                    explanation     = EXCLUDED.explanation,
                    detected_at     = NOW(),
                    monitoring_run_id = EXCLUDED.monitoring_run_id
            """), {
                "ca":          r.case_id_a,
                "cb":          r.case_id_b,
                "ct":          r.correlation_type,
                "strength":    r.strength,
                "shared":      json.dumps(r.shared_entities),
                "explanation": r.explanation,
                "run_id":      run_id or r.monitoring_run_id,
            })
            count += 1
        logger.info("Persisted %d correlation records", count)
        return count

    def load_persisted(
        self,
        session: Session,
        case_id: str | None = None,
        min_strength: float = 0.0,
        limit: int = 100,
    ) -> list[dict]:
        """Retrieves correlation records from the DB."""
        filters = "WHERE cc.strength >= :min_s"
        params: dict = {"min_s": min_strength, "lim": limit}
        if case_id:
            filters += " AND (cc.case_id_a = :cid::uuid OR cc.case_id_b = :cid::uuid)"
            params["cid"] = case_id

        rows = session.execute(text(f"""
            SELECT cc.correlation_id, cc.case_id_a, cc.case_id_b,
                   cc.correlation_type, cc.strength,
                   cc.shared_entities, cc.explanation, cc.detected_at
            FROM audit.cross_case_correlations cc
            {filters}
            ORDER BY cc.strength DESC
            LIMIT :lim
        """), params).mappings().fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _extract_from_graph(
        self,
        graph: ComplianceGraph,
        min_strength: float,
        monitoring_run_id: str | None,
    ) -> list[CorrelationRecord]:
        records = []
        seen_keys: set[str] = set()

        for edge in graph.edges:
            if edge.relationship not in ("shares_pharmacy", "shares_ndc", "shares_provider"):
                continue
            if edge.weight < min_strength:
                continue

            a, b = sorted([edge.source_id, edge.target_id])
            dedup_key = f"{a}:{b}:{edge.relationship}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            records.append(CorrelationRecord(
                case_id_a=a,
                case_id_b=b,
                correlation_type=edge.relationship,
                strength=round(edge.weight, 4),
                shared_entities=edge.properties,
                explanation=_build_explanation(edge.relationship, edge.properties),
                monitoring_run_id=monitoring_run_id,
            ))

        return records

    @staticmethod
    def _merge_multifactor(
        raw: list[CorrelationRecord],
        pair_types: dict[tuple, list[str]],
        monitoring_run_id: str | None,
    ) -> list[CorrelationRecord]:
        """
        For pairs with 2+ correlation types, keep all individual records
        and add a single multi_factor record with boosted strength.
        """
        merged = list(raw)
        seen_multi: set[tuple] = set()

        for pair_key, types in pair_types.items():
            if len(types) < 2:
                continue
            if pair_key in seen_multi:
                continue
            seen_multi.add(pair_key)

            # Find all records for this pair
            a, b = pair_key
            pair_records = [r for r in raw if _pair_key(r.case_id_a, r.case_id_b) == pair_key]
            if not pair_records:
                continue

            max_strength = max(r.strength for r in pair_records)
            # Boost by 0.1 per additional factor, capped at 0.95
            boosted = min(0.95, max_strength + (len(types) - 1) * 0.10)

            merged_entities = {}
            for r in pair_records:
                merged_entities.update(r.shared_entities)

            merged.append(CorrelationRecord(
                case_id_a=a,
                case_id_b=b,
                correlation_type="multi_factor",
                strength=round(boosted, 4),
                shared_entities=merged_entities,
                explanation=(
                    f"Cases share {len(types)} correlation factors: "
                    + ", ".join(sorted(types))
                ),
                monitoring_run_id=monitoring_run_id,
            ))

        return merged


# ------------------------------------------------------------------ #
# Module helpers                                                        #
# ------------------------------------------------------------------ #

def _pair_key(a: str, b: str) -> tuple:
    return tuple(sorted([a, b]))


def _build_explanation(relationship: str, shared: dict) -> str:
    if relationship == "shares_pharmacy":
        pid = shared.get("pharmacy_id", "unknown")
        return f"Both cases involve pharmacy {pid}."
    if relationship == "shares_ndc":
        ndc = shared.get("ndc_11", "unknown")
        return f"Both cases involve NDC {ndc}."
    if relationship == "shares_provider":
        prov = shared.get("provider_id", "unknown")
        return f"Both cases involve provider {prov}."
    if relationship == "shares_entity":
        eid = shared.get("entity_id", "unknown")
        return f"Both cases involve covered entity {eid}."
    if relationship == "multi_factor":
        return "Cases are correlated across multiple shared entities."
    return f"Cases are correlated via {relationship}."
