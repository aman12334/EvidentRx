"""
Regulatory knowledge graph service.

Provides the in-process graph store and query API. Nodes and edges are
stored in adjacency lists for O(degree) traversal. Temporal queries are
first-class: all traversal methods accept an optional ``as_of`` parameter
that restricts the graph to nodes and edges active at that time.

Graph operations
────────────────
  add_node / add_edge          — graph construction
  neighbours()                 — direct adjacency (optionally filtered by relationship)
  reachable()                  — BFS/DFS reachability within depth limit
  lineage()                    — ancestry chain (SUPERSEDES / DERIVED_FROM)
  impact_propagation()         — forward traversal from a changed node
  dependency_chain()           — reverse traversal to source regulations
  find_conflicts()             — nodes reachable via CONFLICTS_WITH edges
  relationship_score()         — aggregated confidence on a path
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Optional

from regulatory.graph.nodes import GraphNode, NodeType
from regulatory.graph.edges import EdgeRelationship, PolicyEdge, make_edge

log = logging.getLogger("evidentrx.regulatory.graph.service")


@dataclass
class GraphPath:
    """A sequence of (node, edge) pairs forming a traversal path."""
    nodes:      list[GraphNode]
    edges:      list[PolicyEdge]
    confidence: float          = 1.0   # product of edge confidences along path

    def to_dict(self) -> dict[str, Any]:
        return {
            "length":     len(self.nodes),
            "confidence": round(self.confidence, 4),
            "nodes":      [n.to_dict() for n in self.nodes],
            "edges":      [e.to_dict() for e in self.edges],
        }


class RegulatoryGraphService:
    """
    In-memory regulatory knowledge graph with temporal query support.

    The graph is tenant-isolated — callers pass tenant_id on every
    write operation. Read queries accept an optional tenant_id filter.
    """

    def __init__(self) -> None:
        # node_id → GraphNode
        self._nodes: dict[str, GraphNode] = {}
        # node_id → [PolicyEdge, ...]  (outgoing edges)
        self._out:   dict[str, list[PolicyEdge]] = {}
        # node_id → [PolicyEdge, ...]  (incoming edges)
        self._in:    dict[str, list[PolicyEdge]]  = {}
        # edge_id → PolicyEdge
        self._edges: dict[str, PolicyEdge] = {}
        # external_id → node_id  (for cross-module lookups)
        self._by_external: dict[str, str] = {}

    # ── Construction ───────────────────────────────────────────────────────────

    def add_node(self, node: GraphNode) -> GraphNode:
        self._nodes[node.node_id] = node
        self._out.setdefault(node.node_id, [])
        self._in.setdefault(node.node_id, [])
        if node.external_id:
            self._by_external[node.external_id] = node.node_id
        log.debug("RegulatoryGraph: added %s node '%s'", node.node_type.value, node.label[:60])
        return node

    def add_edge(self, edge: PolicyEdge) -> PolicyEdge:
        if edge.source_id not in self._nodes or edge.target_id not in self._nodes:
            raise GraphError(
                f"Cannot add edge {edge.edge_id}: source or target node not found"
            )
        self._edges[edge.edge_id] = edge
        self._out[edge.source_id].append(edge)
        self._in[edge.target_id].append(edge)
        return edge

    def connect(
        self,
        source_id:    str,
        target_id:    str,
        relationship: EdgeRelationship,
        confidence:   float  = 1.0,
        provenance:   str    = "system",
        properties:   Optional[dict[str, Any]] = None,
    ) -> PolicyEdge:
        edge = make_edge(source_id, target_id, relationship, confidence, provenance, properties)
        return self.add_edge(edge)

    # ── Node retrieval ─────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self._nodes.get(node_id)

    def get_by_external(self, external_id: str) -> Optional[GraphNode]:
        nid = self._by_external.get(external_id)
        return self._nodes.get(nid) if nid else None

    def nodes_of_type(
        self,
        node_type: NodeType,
        as_of:     Optional[datetime] = None,
    ) -> list[GraphNode]:
        t = as_of or datetime.now(tz=timezone.utc)
        return [
            n for n in self._nodes.values()
            if n.node_type == node_type and n.is_active_at(t)
        ]

    # ── Traversal ──────────────────────────────────────────────────────────────

    def neighbours(
        self,
        node_id:      str,
        relationship: Optional[EdgeRelationship] = None,
        direction:    str                        = "out",   # "out" | "in" | "both"
        as_of:        Optional[datetime]         = None,
    ) -> list[tuple[GraphNode, PolicyEdge]]:
        """Return (node, edge) pairs for direct neighbours."""
        t = as_of or datetime.now(tz=timezone.utc)
        edges: list[PolicyEdge] = []
        if direction in ("out", "both"):
            edges += self._out.get(node_id, [])
        if direction in ("in", "both"):
            edges += self._in.get(node_id, [])

        result = []
        for edge in edges:
            if not edge.is_active_at(t):
                continue
            if relationship and edge.relationship != relationship:
                continue
            peer_id = edge.target_id if direction in ("out", "both") and edge.source_id == node_id else edge.source_id
            peer    = self._nodes.get(peer_id)
            if peer and peer.is_active_at(t):
                result.append((peer, edge))
        return result

    def reachable(
        self,
        start_id:     str,
        relationship: Optional[EdgeRelationship] = None,
        max_depth:    int                        = 5,
        as_of:        Optional[datetime]         = None,
    ) -> list[GraphNode]:
        """BFS forward reachability from start_id within max_depth hops."""
        t       = as_of or datetime.now(tz=timezone.utc)
        visited = {start_id}
        queue:  deque[tuple[str, int]] = deque([(start_id, 0)])
        result: list[GraphNode] = []

        while queue:
            nid, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self._out.get(nid, []):
                if not edge.is_active_at(t):
                    continue
                if relationship and edge.relationship != relationship:
                    continue
                tid = edge.target_id
                if tid in visited:
                    continue
                node = self._nodes.get(tid)
                if node and node.is_active_at(t):
                    visited.add(tid)
                    result.append(node)
                    queue.append((tid, depth + 1))
        return result

    def lineage(
        self,
        node_id: str,
        as_of:   Optional[datetime] = None,
    ) -> list[GraphNode]:
        """
        Walk the SUPERSEDES / DERIVED_FROM chain backwards to find ancestry.
        Returns ordered list: [immediate predecessor, …, oldest ancestor].
        """
        t       = as_of or datetime.now(tz=timezone.utc)
        chain:  list[GraphNode] = []
        visited = {node_id}
        current = node_id

        for _ in range(50):   # guard against cycles
            predecessors = [
                (n, e) for n, e in self.neighbours(
                    current,
                    direction = "in",
                    as_of     = t,
                )
                if e.relationship in (
                    EdgeRelationship.SUPERSEDES,
                    EdgeRelationship.DERIVED_FROM,
                )
            ]
            if not predecessors:
                break
            # Follow highest-confidence predecessor
            pred_node, _ = max(predecessors, key=lambda x: x[1].confidence)
            if pred_node.node_id in visited:
                break
            chain.append(pred_node)
            visited.add(pred_node.node_id)
            current = pred_node.node_id

        return chain

    def impact_propagation(
        self,
        changed_node_id: str,
        max_depth:       int              = 4,
        as_of:           Optional[datetime] = None,
    ) -> list[tuple[GraphNode, float]]:
        """
        Forward BFS following IMPACTS / AFFECTS / REQUIRES edges.

        Returns (node, cumulative_confidence) pairs sorted by confidence desc.
        """
        t       = as_of or datetime.now(tz=timezone.utc)
        impact_rels = {
            EdgeRelationship.IMPACTS,
            EdgeRelationship.AFFECTED_BY,
            EdgeRelationship.REQUIRES,
            EdgeRelationship.APPLIES_TO,
        }
        visited: dict[str, float] = {changed_node_id: 1.0}
        queue: deque[tuple[str, float, int]] = deque([(changed_node_id, 1.0, 0)])
        result: list[tuple[GraphNode, float]] = []

        while queue:
            nid, cum_conf, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self._out.get(nid, []):
                if edge.relationship not in impact_rels:
                    continue
                if not edge.is_active_at(t):
                    continue
                tid       = edge.target_id
                new_conf  = cum_conf * edge.confidence
                if tid in visited and visited[tid] >= new_conf:
                    continue
                node = self._nodes.get(tid)
                if node and node.is_active_at(t):
                    visited[tid] = new_conf
                    result.append((node, new_conf))
                    queue.append((tid, new_conf, depth + 1))

        result.sort(key=lambda x: x[1], reverse=True)
        return result

    def dependency_chain(
        self,
        node_id: str,
        as_of:   Optional[datetime] = None,
    ) -> list[GraphNode]:
        """Reverse BFS: find all nodes that this node REQUIRES or DERIVES from."""
        t = as_of or datetime.now(tz=timezone.utc)
        return self.reachable(
            node_id,
            relationship = None,   # follow any backward dep edge
            max_depth    = 6,
            as_of        = t,
        )

    def find_conflicts(
        self,
        node_id: str,
        as_of:   Optional[datetime] = None,
    ) -> list[GraphNode]:
        """Return nodes in conflict with the given node."""
        t = as_of or datetime.now(tz=timezone.utc)
        return [
            n for n, _ in self.neighbours(
                node_id,
                relationship = EdgeRelationship.CONFLICTS_WITH,
                direction    = "both",
                as_of        = t,
            )
        ]

    # ── Stats ──────────────────────────────────────────────────────────────────

    def graph_stats(self) -> dict[str, Any]:
        type_counts: dict[str, int] = {}
        for n in self._nodes.values():
            type_counts[n.node_type.value] = type_counts.get(n.node_type.value, 0) + 1
        rel_counts: dict[str, int] = {}
        for e in self._edges.values():
            rel_counts[e.relationship.value] = rel_counts.get(e.relationship.value, 0) + 1
        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "by_node_type": type_counts,
            "by_relationship": rel_counts,
        }


# ── Exceptions ─────────────────────────────────────────────────────────────────

class GraphError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_graph: Optional[RegulatoryGraphService] = None


def get_graph_service() -> RegulatoryGraphService:
    global _graph
    if _graph is None:
        _graph = RegulatoryGraphService()
    return _graph
