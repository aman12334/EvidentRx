"""Typed API contracts for graph traversal and visualization endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GraphNodeSchema(BaseModel):
    id:         str
    type:       str     # covered_entity | pharmacy | provider | ndc | finding | case
    label:      str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeSchema(BaseModel):
    source_id:    str
    source_type:  str
    target_id:    str
    target_type:  str
    relationship: str
    weight:       float = 1.0
    properties:   dict[str, Any] = Field(default_factory=dict)


class GraphNeighborhoodResponse(BaseModel):
    root_type:   str
    root_id:     str
    depth:       int
    nodes:       list[GraphNodeSchema] = Field(default_factory=list)
    edges:       list[GraphEdgeSchema] = Field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0


class CentralityNode(BaseModel):
    node_type:       str
    node_id:         str
    label:           str
    degree:          int
    in_degree:       int
    out_degree:      int
    weighted_degree: float


class GraphStatsResponse(BaseModel):
    total_nodes:          int
    total_edges:          int
    nodes_by_type:        dict[str, int]
    edges_by_relationship: dict[str, int]
    top_central_nodes:    list[CentralityNode] = Field(default_factory=list)
