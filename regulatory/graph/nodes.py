"""
Regulatory knowledge graph node types.

The graph connects regulations, compliance rules, workflows, covered
entities, pharmacies, operational policies, investigations, and
remediation guidance. Each node type carries typed metadata and a
temporal validity window so the graph can be replayed at any point in
time.

Node types
──────────
  REGULATION       — a versioned regulatory document (links to RegulatoryDocument)
  COMPLIANCE_RULE  — a deterministic rule in the rules engine
  WORKFLOW         — an investigation or escalation workflow
  COVERED_ENTITY   — a 340B-covered healthcare entity
  PHARMACY         — a covered or contract pharmacy
  POLICY           — an operational policy or internal guidance
  INVESTIGATION    — a specific investigation case
  REMEDIATION      — a remediation action or guidance
  CONCEPT          — an abstract compliance concept (e.g. "carve-in", "GPO prohibition")
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    REGULATION      = "regulation"
    COMPLIANCE_RULE = "compliance_rule"
    WORKFLOW        = "workflow"
    COVERED_ENTITY  = "covered_entity"
    PHARMACY        = "pharmacy"
    POLICY          = "policy"
    INVESTIGATION   = "investigation"
    REMEDIATION     = "remediation"
    CONCEPT         = "concept"


@dataclass
class GraphNode:
    """
    A typed node in the regulatory knowledge graph.

    Temporal validity
    ─────────────────
    valid_from / valid_until allow time-travel queries:
    a node is "active at time T" iff valid_from ≤ T < valid_until
    (valid_until=None means indefinitely active).

    Properties dict stores type-specific attributes — callers should
    use typed subclasses or the typed_props() accessor.
    """
    node_id:     str
    node_type:   NodeType
    label:       str           # human-readable name
    external_id: str | None = None    # e.g. doc_id, rule_code, investigation_id
    valid_from:  datetime      = field(default_factory=lambda: datetime.now(tz=UTC))
    valid_until: datetime | None = None
    properties:  dict[str, Any] = field(default_factory=dict)
    created_at:  datetime       = field(default_factory=lambda: datetime.now(tz=UTC))
    tags:        list[str]      = field(default_factory=list)

    def is_active_at(self, t: datetime) -> bool:
        if t < self.valid_from:
            return False
        if self.valid_until and t >= self.valid_until:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id":     self.node_id,
            "node_type":   self.node_type.value,
            "label":       self.label,
            "external_id": self.external_id,
            "valid_from":  self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "properties":  self.properties,
            "tags":        self.tags,
        }


# ── Typed node constructors ────────────────────────────────────────────────────

def regulation_node(
    doc_id:  str,
    title:   str,
    version: str,
    source:  str,
    domains: list[str],
    effective_date: str | None = None,
    expiry_date:    str | None = None,
) -> GraphNode:
    return GraphNode(
        node_id     = f"gn_{uuid.uuid4().hex[:12]}",
        node_type   = NodeType.REGULATION,
        label       = f"{title} v{version}",
        external_id = doc_id,
        properties  = {
            "title":          title,
            "version":        version,
            "source":         source,
            "domains":        domains,
            "effective_date": effective_date,
            "expiry_date":    expiry_date,
        },
    )


def rule_node(
    rule_code:   str,
    rule_name:   str,
    pack_id:     str,
    domain:      str,
    threshold:   float | None = None,
) -> GraphNode:
    return GraphNode(
        node_id     = f"gn_{uuid.uuid4().hex[:12]}",
        node_type   = NodeType.COMPLIANCE_RULE,
        label       = f"Rule:{rule_code}",
        external_id = rule_code,
        properties  = {
            "rule_code": rule_code,
            "rule_name": rule_name,
            "pack_id":   pack_id,
            "domain":    domain,
            "threshold": threshold,
        },
    )


def workflow_node(
    workflow_id:   str,
    workflow_name: str,
    workflow_type: str,
    version:       str,
) -> GraphNode:
    return GraphNode(
        node_id     = f"gn_{uuid.uuid4().hex[:12]}",
        node_type   = NodeType.WORKFLOW,
        label       = f"WF:{workflow_name}",
        external_id = workflow_id,
        properties  = {
            "workflow_name": workflow_name,
            "workflow_type": workflow_type,
            "version":       version,
        },
    )


def covered_entity_node(
    entity_id: str,
    name:      str,
    entity_type: str,
    state:     str,
    npi:       str | None = None,
) -> GraphNode:
    return GraphNode(
        node_id     = f"gn_{uuid.uuid4().hex[:12]}",
        node_type   = NodeType.COVERED_ENTITY,
        label       = name,
        external_id = entity_id,
        properties  = {
            "entity_type": entity_type,
            "state":       state,
            "npi":         npi,
        },
        tags = [f"state:{state}", f"type:{entity_type}"],
    )


def concept_node(
    concept_name: str,
    description:  str,
    domain:       str,
) -> GraphNode:
    return GraphNode(
        node_id    = f"gn_{uuid.uuid4().hex[:12]}",
        node_type  = NodeType.CONCEPT,
        label      = concept_name,
        properties = {
            "description": description,
            "domain":      domain,
        },
    )
