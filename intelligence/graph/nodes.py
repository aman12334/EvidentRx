"""
Compliance knowledge graph — node type definitions.

Node types represent the major entities in the 340B compliance ecosystem.
Nodes are lightweight dataclasses; the graph is built from DB queries
and held in memory during analysis. No external graph database required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# All valid node types in the compliance knowledge graph
NODE_TYPES = frozenset([
    "covered_entity",
    "pharmacy",
    "provider",
    "ndc",
    "finding",
    "case",
])


@dataclass(kw_only=True)
class GraphNode:
    id:         str            # UUID string or natural key (e.g. NDC-11)
    type:       str            # one of NODE_TYPES
    label:      str            # human-readable name/code
    properties: dict = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash((self.type, self.id))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GraphNode):
            return False
        return self.type == other.type and self.id == other.id

    def key(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(kw_only=True)
class CoveredEntityNode(GraphNode):
    type: str = "covered_entity"

    @classmethod
    def from_row(cls, row: dict) -> "CoveredEntityNode":
        return cls(
            id=str(row["ce_id"]),
            type="covered_entity",
            label=row.get("entity_name", str(row["ce_id"])),
            properties={
                "program_type":   row.get("program_type"),
                "state":          row.get("state"),
                "program_start":  str(row.get("program_participation_start", "")),
            },
        )


@dataclass(kw_only=True)
class PharmacyNode(GraphNode):
    type: str = "pharmacy"

    @classmethod
    def from_row(cls, row: dict) -> "PharmacyNode":
        return cls(
            id=str(row["pharmacy_id"]),
            type="pharmacy",
            label=row.get("pharmacy_name", str(row["pharmacy_id"])),
            properties={
                "npi":            row.get("npi"),
                "state":          row.get("state"),
                "contract_start": str(row.get("contract_start_date", "")),
                "contract_end":   str(row.get("contract_end_date", "")),
            },
        )


@dataclass(kw_only=True)
class ProviderNode(GraphNode):
    type: str = "provider"

    @classmethod
    def from_row(cls, row: dict) -> "ProviderNode":
        return cls(
            id=str(row["provider_id"]),
            type="provider",
            label=row.get("npi", str(row["provider_id"])),
            properties={
                "npi":      row.get("npi"),
                "taxonomy": row.get("primary_taxonomy"),
            },
        )


@dataclass(kw_only=True)
class NdcNode(GraphNode):
    type: str = "ndc"

    @classmethod
    def from_ndc_11(cls, ndc_11: str, drug_name: str = "") -> "NdcNode":
        return cls(
            id=ndc_11,
            type="ndc",
            label=drug_name or ndc_11,
            properties={"ndc_11": ndc_11},
        )


@dataclass(kw_only=True)
class FindingNode(GraphNode):
    type: str = "finding"

    @classmethod
    def from_row(cls, row: dict) -> "FindingNode":
        return cls(
            id=str(row["finding_id"]),
            type="finding",
            label=row.get("finding_code", str(row["finding_id"])),
            properties={
                "rule_code":  row.get("rule_code"),
                "severity":   row.get("severity"),
                "service_date": str(row.get("service_date", "")),
                "ndc_11":     row.get("ndc_11"),
            },
        )


@dataclass(kw_only=True)
class CaseNode(GraphNode):
    type: str = "case"

    @classmethod
    def from_row(cls, row: dict) -> "CaseNode":
        return cls(
            id=str(row["case_id"]),
            type="case",
            label=row.get("case_number", str(row["case_id"])),
            properties={
                "status":   row.get("status"),
                "priority": row.get("priority"),
                "category": row.get("violation_category"),
            },
        )
