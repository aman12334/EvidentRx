import type { GraphNeighborhoodResponse, GraphStatsResponse } from "@/lib/types/graph";
import { apiClient } from "./client";

export async function getGraphStats(): Promise<GraphStatsResponse> {
  const { data } = await apiClient.get("/graph/stats");
  return data;
}

export async function getNeighborhood(
  nodeType: string,
  nodeId: string,
  depth = 2
): Promise<GraphNeighborhoodResponse> {
  const { data } = await apiClient.get(
    `/graph/neighborhood/${nodeType}/${nodeId}`,
    { params: { depth } }
  );
  return data;
}

export async function getCaseCorrelations(
  caseId: string,
  minStrength = 0.2
): Promise<{ case_id: string; correlations: unknown[] }> {
  const { data } = await apiClient.get(`/graph/correlations/${caseId}`, {
    params: { min_strength: minStrength },
  });
  return data;
}
