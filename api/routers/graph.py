"""Graph traversal and visualization API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.schemas.graph import GraphNeighborhoodResponse, GraphStatsResponse
from app.database import get_db
from intelligence.graph.builder import ComplianceGraphBuilder
from intelligence.graph.traversal import GraphTraversalService

router = APIRouter(prefix="/graph", tags=["Graph"])

_builder   = ComplianceGraphBuilder()
_traversal = GraphTraversalService()


@router.get("/stats", response_model=GraphStatsResponse)
def get_graph_stats(db: Session = Depends(get_db)):
    """Returns overall compliance graph statistics."""
    graph      = _builder.build(db)
    stats      = graph.stats()
    centrality = _traversal.compute_centrality(graph, top_n=10)

    return GraphStatsResponse(
        total_nodes=stats["total_nodes"],
        total_edges=stats["total_edges"],
        nodes_by_type=stats["nodes_by_type"],
        edges_by_relationship=stats["edges_by_relationship"],
        top_central_nodes=[
            {
                "node_type":       c.node_type,
                "node_id":         c.node_id,
                "label":           c.label,
                "degree":          c.degree,
                "in_degree":       c.in_degree,
                "out_degree":      c.out_degree,
                "weighted_degree": c.weighted_degree,
            }
            for c in centrality
        ],
    )


@router.get("/neighborhood/{node_type}/{node_id}", response_model=GraphNeighborhoodResponse)
def get_neighborhood(
    node_type: str,
    node_id:   str,
    depth:     int = Query(2, ge=1, le=3),
    db:        Session = Depends(get_db),
):
    """
    Returns the graph neighborhood for a node up to the given depth.
    Supports: case, covered_entity, pharmacy, ndc, finding, provider.
    """
    graph  = _builder.build(db)
    result = _traversal.bfs(graph, node_type, node_id, max_depth=depth)

    nodes = [
        {"id": n.id, "type": n.type, "label": n.label, "properties": n.properties}
        for n in result.visited
    ]

    edges = [
        {
            "source_id":   e.source_id,
            "source_type": e.source_type,
            "target_id":   e.target_id,
            "target_type": e.target_type,
            "relationship": e.relationship,
            "weight":       e.weight,
            "properties":   e.properties,
        }
        for e in graph.edges
        if f"{e.source_type}:{e.source_id}" in {n["type"] + ":" + n["id"] for n in nodes}
        or f"{e.target_type}:{e.target_id}" in {n["type"] + ":" + n["id"] for n in nodes}
    ]

    return GraphNeighborhoodResponse(
        root_type=node_type,
        root_id=node_id,
        depth=depth,
        nodes=nodes,
        edges=edges,
        total_nodes=len(nodes),
        total_edges=len(edges),
    )


@router.get("/correlations/{case_id}")
def get_case_correlations(
    case_id:      str,
    min_strength: float = Query(0.2, ge=0.0, le=1.0),
    db:           Session = Depends(get_db),
):
    """Returns cases correlated to the given case."""
    graph  = _builder.build(db)
    result = _traversal.find_correlated_cases(graph, case_id, min_strength=min_strength)
    return {"case_id": case_id, "correlations": result}
