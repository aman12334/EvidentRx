import { useQuery } from "@tanstack/react-query";
import { getGraphStats, getNeighborhood, getCaseCorrelations } from "@/lib/api/graph";

export function useGraphStats() {
  return useQuery({
    queryKey: ["graph", "stats"],
    queryFn:  getGraphStats,
  });
}

export function useNeighborhood(nodeType: string, nodeId: string) {
  return useQuery({
    queryKey: ["graph", "neighborhood", nodeType, nodeId],
    queryFn:  () => getNeighborhood(nodeType, nodeId),
    enabled:  Boolean(nodeType && nodeId),
  });
}

export function useCaseCorrelations(caseId: string) {
  return useQuery({
    queryKey: ["graph", "correlations", caseId],
    queryFn:  () => getCaseCorrelations(caseId),
    enabled:  Boolean(caseId),
  });
}
