import { useQuery } from "@tanstack/react-query";
import { getEntityRiskScores, getIntelligenceSummary, getMonitoringRuns } from "@/lib/api/monitoring";

export function useIntelligenceSummary(window: "30d" | "60d" | "90d" = "30d") {
  return useQuery({
    queryKey: ["intelligence-summary", window],
    queryFn:  () => getIntelligenceSummary(window),
    staleTime: 300_000,
  });
}

export function useMonitoringRuns() {
  return useQuery({
    queryKey: ["monitoring-runs"],
    queryFn:  () => getMonitoringRuns(10),
    staleTime: 60_000,
  });
}

export function useEntityRiskScores(tier?: string) {
  return useQuery({
    queryKey: ["risk-scores", tier],
    queryFn:  () => getEntityRiskScores({ tier, limit: 25 }),
    staleTime: 300_000,
  });
}
