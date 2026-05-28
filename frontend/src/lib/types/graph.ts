export interface GraphNode {
  id:         string;
  type:       string;
  label:      string;
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  source_id:    string;
  source_type:  string;
  target_id:    string;
  target_type:  string;
  relationship: string;
  weight:       number;
  properties:   Record<string, unknown>;
}

export interface GraphNeighborhoodResponse {
  root_type:   string;
  root_id:     string;
  depth:       number;
  nodes:       GraphNode[];
  edges:       GraphEdge[];
  total_nodes: number;
  total_edges: number;
}

export interface CentralityNode {
  node_type:       string;
  node_id:         string;
  label:           string;
  degree:          number;
  in_degree:       number;
  out_degree:      number;
  weighted_degree: number;
}

export interface GraphStatsResponse {
  total_nodes:           number;
  total_edges:           number;
  nodes_by_type:         Record<string, number>;
  edges_by_relationship: Record<string, number>;
  top_central_nodes:     CentralityNode[];
}
