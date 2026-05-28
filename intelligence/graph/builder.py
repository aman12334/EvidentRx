"""
ComplianceGraphBuilder — builds the in-memory compliance knowledge graph from DB.

The graph is rebuilt on demand from PostgreSQL queries. It is not persisted
in memory between requests — each analysis session builds a fresh graph.
Edges are also written to audit.intelligence_graph_edges for lineage tracking.

Graph population strategy:
  1. Load all open/active investigation cases
  2. For each case, load linked findings
  3. Extract entity relationships from evidence_payload
  4. Build edges: finding→pharmacy, finding→ndc, finding→entity, finding→case
  5. Compute case-to-case correlation edges from shared nodes
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from intelligence.graph.edges import GraphEdge
from intelligence.graph.nodes import (
    CaseNode,
    CoveredEntityNode,
    FindingNode,
    GraphNode,
    NdcNode,
    PharmacyNode,
)

logger = logging.getLogger(__name__)


class ComplianceGraph:
    """
    In-memory compliance knowledge graph.

    Nodes: dict[key, GraphNode]  where key = "{type}:{id}"
    Edges: list[GraphEdge]
    Adjacency: dict[source_key, list[GraphEdge]]  (outgoing edges)
    Reverse:   dict[target_key, list[GraphEdge]]  (incoming edges)
    """

    def __init__(self) -> None:
        self.nodes:     dict[str, GraphNode]      = {}
        self.edges:     list[GraphEdge]           = []
        self._adj:      dict[str, list[GraphEdge]] = defaultdict(list)
        self._rev:      dict[str, list[GraphEdge]] = defaultdict(list)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.key()] = node

    def add_edge(self, edge: GraphEdge) -> None:
        src_key = f"{edge.source_type}:{edge.source_id}"
        tgt_key = f"{edge.target_type}:{edge.target_id}"
        self.edges.append(edge)
        self._adj[src_key].append(edge)
        self._rev[tgt_key].append(edge)

    def outgoing(self, node_type: str, node_id: str) -> list[GraphEdge]:
        return self._adj.get(f"{node_type}:{node_id}", [])

    def incoming(self, node_type: str, node_id: str) -> list[GraphEdge]:
        return self._rev.get(f"{node_type}:{node_id}", [])

    def neighbors(self, node_type: str, node_id: str) -> list[GraphNode]:
        """Returns all nodes reachable by one outgoing edge."""
        result = []
        for edge in self.outgoing(node_type, node_id):
            key = f"{edge.target_type}:{edge.target_id}"
            if key in self.nodes:
                result.append(self.nodes[key])
        return result

    def stats(self) -> dict:
        type_counts: dict[str, int] = defaultdict(int)
        for node in self.nodes.values():
            type_counts[node.type] += 1
        rel_counts: dict[str, int] = defaultdict(int)
        for edge in self.edges:
            rel_counts[edge.relationship] += 1
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "nodes_by_type": dict(type_counts),
            "edges_by_relationship": dict(rel_counts),
        }


class ComplianceGraphBuilder:
    """
    Builds a ComplianceGraph from PostgreSQL and optionally persists
    new edges to audit.intelligence_graph_edges.
    """

    def build(
        self,
        session: Session,
        persist_edges: bool = False,
        status_filter: tuple[str, ...] = ("open", "triaged", "investigating", "escalated"),
    ) -> ComplianceGraph:
        graph = ComplianceGraph()

        cases    = self._load_cases(session, status_filter)
        findings = self._load_findings(session, [str(c["case_id"]) for c in cases])

        # Add case nodes
        for c in cases:
            graph.add_node(CaseNode.from_row(c))

        # Index findings by case
        findings_by_case: dict[str, list[dict]] = defaultdict(list)
        for f in findings:
            findings_by_case[str(f["case_id"])].append(f)

        # Track entity nodes seen (dedup)
        seen_entities: set[str] = set()
        seen_pharmacies: set[str] = set()
        seen_ndcs: set[str] = set()

        for f in findings:
            fid      = str(f["finding_id"])
            cid      = str(f["case_id"])
            eid      = str(f["covered_entity_id"])
            severity = f.get("severity", "medium")
            ndc_11   = f.get("ndc_11")
            evidence = f.get("evidence_payload") or {}

            # Add finding node
            graph.add_node(FindingNode.from_row(f))

            # finding → case edge
            graph.add_edge(GraphEdge.finding_to_case(fid, cid))

            # finding → covered entity edge
            if eid not in seen_entities:
                graph.add_node(CoveredEntityNode(
                    id=eid, type="covered_entity",
                    label=f.get("entity_name", eid),
                ))
                seen_entities.add(eid)
            graph.add_edge(GraphEdge.finding_to_entity(fid, eid, severity))

            # finding → NDC edge (from ndc_11 column or evidence_payload)
            ndc = ndc_11 or evidence.get("ndc_11")
            if ndc:
                if ndc not in seen_ndcs:
                    graph.add_node(NdcNode.from_ndc_11(ndc))
                    seen_ndcs.add(ndc)
                graph.add_edge(GraphEdge.finding_to_ndc(fid, ndc, severity))

            # finding → pharmacy edge (from evidence_payload)
            pharmacy_id = evidence.get("pharmacy_id") or evidence.get("contract_pharmacy_id")
            if pharmacy_id and pharmacy_id not in seen_pharmacies:
                graph.add_node(PharmacyNode(
                    id=str(pharmacy_id), type="pharmacy",
                    label=evidence.get("pharmacy_name", str(pharmacy_id)),
                ))
                seen_pharmacies.add(str(pharmacy_id))
            if pharmacy_id:
                graph.add_edge(GraphEdge.finding_to_pharmacy(fid, str(pharmacy_id), severity))

        # Build case-to-case correlation edges (shared entities)
        self._add_case_correlations(graph, findings_by_case)

        if persist_edges:
            self._persist_edges(session, graph)

        logger.info("Graph built: %s", graph.stats())
        return graph

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_cases(
        self,
        session: Session,
        status_filter: tuple[str, ...],
    ) -> list[dict]:
        placeholders = ", ".join(f":s{i}" for i in range(len(status_filter)))
        params = {f"s{i}": s for i, s in enumerate(status_filter)}
        rows = session.execute(text(f"""
            SELECT ic.case_id, ic.case_number, ic.covered_entity_id,
                   ic.violation_category, ic.status, ic.priority,
                   ce.entity_name
            FROM audit.investigation_cases ic
            LEFT JOIN ref.covered_entities ce
                   ON ic.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
            WHERE ic.status IN ({placeholders})
        """), params).mappings().fetchall()
        return [dict(r) for r in rows]

    def _load_findings(self, session: Session, case_ids: list[str]) -> list[dict]:
        if not case_ids:
            return []
        placeholders = ", ".join(f":c{i}" for i in range(len(case_ids)))
        params = {f"c{i}": c for i, c in enumerate(case_ids)}
        rows = session.execute(text(f"""
            SELECT af.finding_id, icf.case_id, af.covered_entity_id,
                   af.finding_code, af.rule_code, af.severity,
                   af.evidence_payload, af.entity_references,
                   ce.entity_name,
                   sb.ndc_11
            FROM audit.investigation_case_findings icf
            JOIN audit.audit_findings af ON icf.finding_id = af.finding_id
            LEFT JOIN ref.covered_entities ce
                   ON af.covered_entity_id = ce.ce_id AND ce.is_current = TRUE
            LEFT JOIN ops.split_billing sb ON af.split_billing_id = sb.split_billing_id
            WHERE icf.case_id IN ({placeholders})
        """), params).mappings().fetchall()
        return [dict(r) for r in rows]

    def _add_case_correlations(
        self,
        graph: ComplianceGraph,
        findings_by_case: dict[str, list[dict]],
    ) -> None:
        """Adds case↔case edges where cases share pharmacies or NDCs."""
        # Index: pharmacy_id → set of case_ids
        pharmacy_to_cases: dict[str, set[str]] = defaultdict(set)
        ndc_to_cases:      dict[str, set[str]] = defaultdict(set)

        for case_id, findings in findings_by_case.items():
            for f in findings:
                ev = f.get("evidence_payload") or {}
                pid = ev.get("pharmacy_id") or ev.get("contract_pharmacy_id")
                if pid:
                    pharmacy_to_cases[str(pid)].add(case_id)
                ndc = f.get("ndc_11") or ev.get("ndc_11")
                if ndc:
                    ndc_to_cases[ndc].add(case_id)

        # Add shared_pharmacy edges between all pairs sharing a pharmacy
        for pid, case_set in pharmacy_to_cases.items():
            cases = sorted(case_set)
            for i, ca in enumerate(cases):
                for cb in cases[i+1:]:
                    strength = min(0.9, 0.3 + len(case_set) * 0.1)
                    graph.add_edge(GraphEdge.case_correlation(
                        ca, cb, "shares_pharmacy", strength,
                        pharmacy_id=pid,
                    ))

        # Add shared_ndc edges
        for ndc, case_set in ndc_to_cases.items():
            cases = sorted(case_set)
            for i, ca in enumerate(cases):
                for cb in cases[i+1:]:
                    strength = min(0.7, 0.2 + len(case_set) * 0.05)
                    graph.add_edge(GraphEdge.case_correlation(
                        ca, cb, "shares_ndc", strength,
                        ndc_11=ndc,
                    ))

    def _persist_edges(self, session: Session, graph: ComplianceGraph) -> None:
        today = date.today()
        rows = []
        for edge in graph.edges:
            rows.append({
                "source_type":  edge.source_type,
                "source_id":    edge.source_id,
                "target_type":  edge.target_type,
                "target_id":    edge.target_id,
                "relationship": edge.relationship,
                "weight":       float(edge.weight),
                "properties":   edge.properties,
                "valid_from":   today.isoformat(),
            })
        if not rows:
            return
        # Upsert with ON CONFLICT DO UPDATE weight
        for row in rows:
            session.execute(text("""
                INSERT INTO audit.intelligence_graph_edges
                    (source_type, source_id, target_type, target_id,
                     relationship, weight, properties, valid_from)
                VALUES
                    (:source_type, :source_id, :target_type, :target_id,
                     :relationship, :weight, :properties::jsonb, :valid_from::date)
                ON CONFLICT (source_type, source_id, target_type, target_id, relationship)
                DO UPDATE SET weight = EXCLUDED.weight, properties = EXCLUDED.properties
            """), row)
        logger.info("Persisted %d graph edges", len(rows))
