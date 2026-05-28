"""
GraphTraversalService — BFS/DFS traversal, centrality scoring,
neighborhood analysis, and temporal evolution over the compliance graph.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from intelligence.graph.builder import ComplianceGraph
from intelligence.graph.nodes import GraphNode

logger = logging.getLogger(__name__)


@dataclass
class TraversalResult:
    root_type:   str
    root_id:     str
    depth:       int
    visited:     list[GraphNode] = field(default_factory=list)
    edges_used:  int = 0
    relationships_seen: set[str] = field(default_factory=set)

    def summary(self) -> dict:
        by_type: dict[str, int] = defaultdict(int)
        for node in self.visited:
            by_type[node.type] += 1
        return {
            "root":               f"{self.root_type}:{self.root_id}",
            "depth":              self.depth,
            "total_nodes":        len(self.visited),
            "nodes_by_type":      dict(by_type),
            "edges_traversed":    self.edges_used,
            "relationships_seen": sorted(self.relationships_seen),
        }


@dataclass
class CentralityResult:
    node_type:  str
    node_id:    str
    label:      str
    degree:     int               # total edges (in + out)
    in_degree:  int
    out_degree: int
    weighted_degree: float        # sum of edge weights


@dataclass
class NeighborhoodResult:
    node_type:   str
    node_id:     str
    depth_1:     list[GraphNode]
    depth_2:     list[GraphNode]
    case_count:  int
    finding_count: int
    shared_pharmacies: list[str]
    shared_ndcs: list[str]


class GraphTraversalService:

    def bfs(
        self,
        graph: ComplianceGraph,
        start_type: str,
        start_id: str,
        max_depth: int = 2,
        relationship_filter: Optional[set[str]] = None,
    ) -> TraversalResult:
        """
        Breadth-first traversal from a start node up to max_depth hops.
        Returns all reachable nodes and traversal metadata.
        """
        result = TraversalResult(root_type=start_type, root_id=start_id, depth=max_depth)
        visited_keys: set[str] = set()
        queue: deque[tuple[str, str, int]] = deque()

        root_key = f"{start_type}:{start_id}"
        queue.append((start_type, start_id, 0))
        visited_keys.add(root_key)

        if root_key in graph.nodes:
            result.visited.append(graph.nodes[root_key])

        while queue:
            node_type, node_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            for edge in graph.outgoing(node_type, node_id):
                if relationship_filter and edge.relationship not in relationship_filter:
                    continue
                target_key = f"{edge.target_type}:{edge.target_id}"
                result.relationships_seen.add(edge.relationship)
                result.edges_used += 1
                if target_key not in visited_keys:
                    visited_keys.add(target_key)
                    if target_key in graph.nodes:
                        result.visited.append(graph.nodes[target_key])
                    queue.append((edge.target_type, edge.target_id, depth + 1))

        return result

    def find_recurring_pharmacies(
        self,
        graph: ComplianceGraph,
        min_cases: int = 2,
    ) -> list[dict]:
        """
        Returns pharmacies that appear in findings across min_cases or more
        distinct investigation cases.
        """
        pharmacy_cases: dict[str, set[str]] = defaultdict(set)
        pharmacy_labels: dict[str, str] = {}

        for edge in graph.edges:
            if edge.relationship == "involves_pharmacy":
                # finding → pharmacy; find which case this finding belongs to
                finding_key = f"{edge.source_type}:{edge.source_id}"
                for case_edge in graph.outgoing("finding", edge.source_id):
                    if case_edge.relationship == "grouped_in_case":
                        pharmacy_cases[edge.target_id].add(case_edge.target_id)
                pharmacy_node = graph.nodes.get(f"pharmacy:{edge.target_id}")
                if pharmacy_node:
                    pharmacy_labels[edge.target_id] = pharmacy_node.label

        results = []
        for pharmacy_id, case_set in pharmacy_cases.items():
            if len(case_set) >= min_cases:
                results.append({
                    "pharmacy_id":  pharmacy_id,
                    "label":        pharmacy_labels.get(pharmacy_id, pharmacy_id),
                    "case_count":   len(case_set),
                    "case_ids":     sorted(case_set),
                })

        return sorted(results, key=lambda r: r["case_count"], reverse=True)

    def find_correlated_cases(
        self,
        graph: ComplianceGraph,
        case_id: str,
        min_strength: float = 0.2,
    ) -> list[dict]:
        """
        Returns cases correlated to the given case, ordered by strength.
        """
        results = []
        for edge in graph.outgoing("case", case_id):
            if edge.relationship in ("correlated_with", "shares_pharmacy", "shares_ndc", "shares_provider"):
                if edge.weight >= min_strength:
                    target_node = graph.nodes.get(f"case:{edge.target_id}")
                    results.append({
                        "case_id":       edge.target_id,
                        "label":         target_node.label if target_node else edge.target_id,
                        "relationship":  edge.relationship,
                        "strength":      round(float(edge.weight), 4),
                        "shared":        edge.properties,
                    })
        # Also check reverse
        for edge in graph.incoming("case", case_id):
            if edge.relationship in ("correlated_with", "shares_pharmacy", "shares_ndc", "shares_provider"):
                if edge.weight >= min_strength:
                    source_node = graph.nodes.get(f"case:{edge.source_id}")
                    results.append({
                        "case_id":       edge.source_id,
                        "label":         source_node.label if source_node else edge.source_id,
                        "relationship":  edge.relationship,
                        "strength":      round(float(edge.weight), 4),
                        "shared":        edge.properties,
                    })

        # Dedup by case_id, keep highest strength
        seen: dict[str, dict] = {}
        for r in results:
            cid = r["case_id"]
            if cid not in seen or r["strength"] > seen[cid]["strength"]:
                seen[cid] = r
        return sorted(seen.values(), key=lambda x: x["strength"], reverse=True)

    def compute_centrality(
        self,
        graph: ComplianceGraph,
        node_type: Optional[str] = None,
        top_n: int = 20,
    ) -> list[CentralityResult]:
        """
        Computes degree centrality for all nodes (or a specific type).
        High centrality = hub in the compliance network.
        """
        centrality: dict[str, CentralityResult] = {}

        for node in graph.nodes.values():
            if node_type and node.type != node_type:
                continue
            key = node.key()
            out_edges = graph.outgoing(node.type, node.id)
            in_edges  = graph.incoming(node.type, node.id)
            weighted  = sum(e.weight for e in out_edges + in_edges)
            centrality[key] = CentralityResult(
                node_type=node.type,
                node_id=node.id,
                label=node.label,
                degree=len(out_edges) + len(in_edges),
                in_degree=len(in_edges),
                out_degree=len(out_edges),
                weighted_degree=round(weighted, 4),
            )

        ranked = sorted(centrality.values(), key=lambda x: x.weighted_degree, reverse=True)
        return ranked[:top_n]

    def neighborhood_analysis(
        self,
        graph: ComplianceGraph,
        node_type: str,
        node_id: str,
    ) -> NeighborhoodResult:
        """
        Returns a structured neighborhood summary for a node.
        """
        depth1 = graph.neighbors(node_type, node_id)
        depth2 = []
        for n in depth1:
            depth2.extend(graph.neighbors(n.type, n.id))

        # Dedup depth2 (remove depth1 nodes and root)
        root_key = f"{node_type}:{node_id}"
        depth1_keys = {n.key() for n in depth1}
        depth2 = [n for n in depth2 if n.key() != root_key and n.key() not in depth1_keys]

        all_nodes = depth1 + depth2
        case_count    = sum(1 for n in all_nodes if n.type == "case")
        finding_count = sum(1 for n in all_nodes if n.type == "finding")

        shared_pharmacies = list({n.id for n in all_nodes if n.type == "pharmacy"})
        shared_ndcs       = list({n.id for n in all_nodes if n.type == "ndc"})

        return NeighborhoodResult(
            node_type=node_type,
            node_id=node_id,
            depth_1=depth1,
            depth_2=depth2,
            case_count=case_count,
            finding_count=finding_count,
            shared_pharmacies=shared_pharmacies,
            shared_ndcs=shared_ndcs,
        )

    def find_recurring_ndc_violations(
        self,
        graph: ComplianceGraph,
        min_cases: int = 2,
    ) -> list[dict]:
        """NDCs involved in findings across multiple cases."""
        ndc_cases: dict[str, set[str]] = defaultdict(set)
        ndc_labels: dict[str, str] = {}

        for edge in graph.edges:
            if edge.relationship == "involves_ndc":
                for case_edge in graph.outgoing("finding", edge.source_id):
                    if case_edge.relationship == "grouped_in_case":
                        ndc_cases[edge.target_id].add(case_edge.target_id)
                ndc_node = graph.nodes.get(f"ndc:{edge.target_id}")
                if ndc_node:
                    ndc_labels[edge.target_id] = ndc_node.label

        results = []
        for ndc, case_set in ndc_cases.items():
            if len(case_set) >= min_cases:
                results.append({
                    "ndc_11":     ndc,
                    "label":      ndc_labels.get(ndc, ndc),
                    "case_count": len(case_set),
                    "case_ids":   sorted(case_set),
                })
        return sorted(results, key=lambda r: r["case_count"], reverse=True)
