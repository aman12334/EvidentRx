"""
Compliance knowledge graph — edge type definitions and relationship scoring.

Edges are directed and weighted. Weight reflects the strength of the
relationship (e.g. number of shared findings, frequency of co-occurrence).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Relationship types between node types
RELATIONSHIP_TYPES = frozenset([
    "has_contract",         # covered_entity → pharmacy
    "involves_pharmacy",    # finding → pharmacy
    "involves_provider",    # finding → provider
    "involves_ndc",         # finding → ndc
    "belongs_to_entity",    # finding → covered_entity
    "grouped_in_case",      # finding → case
    "investigated_by",      # case → covered_entity
    "correlated_with",      # case ↔ case
    "shares_pharmacy",      # case ↔ case (same pharmacy involved)
    "shares_ndc",           # case ↔ case (same NDC)
    "shares_provider",      # case ↔ case (same provider)
])

# Relationship weight modifiers by severity
_SEVERITY_WEIGHT = {
    "critical": 4.0,
    "high":     2.0,
    "medium":   1.0,
    "low":      0.5,
}


@dataclass
class GraphEdge:
    source_type:  str
    source_id:    str
    target_type:  str
    target_id:    str
    relationship: str
    weight:       float = 1.0
    properties:   dict  = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.source_type}:{self.source_id}→{self.relationship}→{self.target_type}:{self.target_id}"

    @classmethod
    def finding_to_pharmacy(
        cls,
        finding_id: str,
        pharmacy_id: str,
        severity: str = "medium",
        **props,
    ) -> GraphEdge:
        return cls(
            source_type="finding", source_id=finding_id,
            target_type="pharmacy", target_id=pharmacy_id,
            relationship="involves_pharmacy",
            weight=_SEVERITY_WEIGHT.get(severity, 1.0),
            properties=props,
        )

    @classmethod
    def finding_to_ndc(
        cls,
        finding_id: str,
        ndc_11: str,
        severity: str = "medium",
        **props,
    ) -> GraphEdge:
        return cls(
            source_type="finding", source_id=finding_id,
            target_type="ndc", target_id=ndc_11,
            relationship="involves_ndc",
            weight=_SEVERITY_WEIGHT.get(severity, 1.0),
            properties=props,
        )

    @classmethod
    def finding_to_entity(
        cls,
        finding_id: str,
        entity_id: str,
        severity: str = "medium",
    ) -> GraphEdge:
        return cls(
            source_type="finding", source_id=finding_id,
            target_type="covered_entity", target_id=entity_id,
            relationship="belongs_to_entity",
            weight=_SEVERITY_WEIGHT.get(severity, 1.0),
        )

    @classmethod
    def finding_to_case(cls, finding_id: str, case_id: str) -> GraphEdge:
        return cls(
            source_type="finding", source_id=finding_id,
            target_type="case", target_id=case_id,
            relationship="grouped_in_case",
            weight=1.0,
        )

    @classmethod
    def case_correlation(
        cls,
        case_id_a: str,
        case_id_b: str,
        relationship: str,
        strength: float,
        **props,
    ) -> GraphEdge:
        return cls(
            source_type="case", source_id=case_id_a,
            target_type="case", target_id=case_id_b,
            relationship=relationship,
            weight=strength,
            properties=props,
        )


def compute_edge_weight(
    n_shared: int,
    severity_mix: dict[str, int],
    base: float = 1.0,
) -> float:
    """
    Computes edge weight from the number of shared findings and their severity.
    Used for cross-case correlation strength.
    """
    severity_score = sum(
        _SEVERITY_WEIGHT.get(sev, 1.0) * count
        for sev, count in severity_mix.items()
    )
    return min(1.0, base * (n_shared * 0.1) * (severity_score / max(n_shared, 1)))
