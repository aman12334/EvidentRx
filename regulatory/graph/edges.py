"""
Regulatory knowledge graph edge types.

Edges encode typed, directional relationships between graph nodes.
Each edge carries a confidence score, a validity window, and a
provenance record (which document or analyst assertion created it).

Edge relationship types
───────────────────────
  REGULATES          — regulation → rule/workflow/entity
  SUPERSEDES         — newer regulation → older regulation
  REQUIRES           — rule → another rule (dependency)
  IMPACTS            — regulation/policy → workflow/entity
  CITES              — investigation/workflow → regulation (policy citation)
  APPLIES_TO         — rule/workflow → covered_entity/pharmacy
  IMPLEMENTS         — workflow → regulation (implements the requirement)
  CONFLICTS_WITH     — regulation/rule → conflicting regulation/rule
  DERIVED_FROM       — rule/concept → source regulation
  MITIGATED_BY       — risk/investigation → remediation
  AFFECTED_BY        — entity/workflow → policy change (impact linkage)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional


class EdgeRelationship(str, Enum):
    REGULATES      = "regulates"
    SUPERSEDES     = "supersedes"
    REQUIRES       = "requires"
    IMPACTS        = "impacts"
    CITES          = "cites"
    APPLIES_TO     = "applies_to"
    IMPLEMENTS     = "implements"
    CONFLICTS_WITH = "conflicts_with"
    DERIVED_FROM   = "derived_from"
    MITIGATED_BY   = "mitigated_by"
    AFFECTED_BY    = "affected_by"


@dataclass
class PolicyEdge:
    """
    A directed, typed, scored edge in the regulatory knowledge graph.

    Confidence score
    ────────────────
    0.0 = asserted with no evidence
    0.5 = inferred heuristically
    1.0 = confirmed by authoritative source (human or verified document)

    Temporal validity mirrors node convention.
    """
    edge_id:      str
    source_id:    str                 # source node_id
    target_id:    str                 # target node_id
    relationship: EdgeRelationship
    confidence:   float               = 1.0
    valid_from:   datetime            = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    valid_until:  Optional[datetime]  = None
    provenance:   str                 = "system"   # doc_id | analyst_id | "system"
    properties:   dict[str, Any]      = field(default_factory=dict)
    created_at:   datetime            = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def is_active_at(self, t: datetime) -> bool:
        if t < self.valid_from:
            return False
        if self.valid_until and t >= self.valid_until:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id":      self.edge_id,
            "source_id":    self.source_id,
            "target_id":    self.target_id,
            "relationship": self.relationship.value,
            "confidence":   round(self.confidence, 4),
            "valid_from":   self.valid_from.isoformat(),
            "valid_until":  self.valid_until.isoformat() if self.valid_until else None,
            "provenance":   self.provenance,
            "properties":   self.properties,
        }


def make_edge(
    source_id:    str,
    target_id:    str,
    relationship: EdgeRelationship,
    confidence:   float              = 1.0,
    provenance:   str                = "system",
    properties:   Optional[dict[str, Any]] = None,
) -> PolicyEdge:
    return PolicyEdge(
        edge_id      = f"ge_{uuid.uuid4().hex[:12]}",
        source_id    = source_id,
        target_id    = target_id,
        relationship = relationship,
        confidence   = confidence,
        provenance   = provenance,
        properties   = properties or {},
    )
