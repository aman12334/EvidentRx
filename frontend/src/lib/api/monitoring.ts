import type { EntityRiskScore, IntelligenceSummary, MonitoringRun } from "@/lib/types/monitoring";
import { apiClient } from "./client";

export async function getIntelligenceSummary(
  window: "30d" | "60d" | "90d" = "30d"
): Promise<IntelligenceSummary> {
  const { data } = await apiClient.get("/monitoring/summary", { params: { window } });
  return data;
}

export async function getMonitoringRuns(limit = 10): Promise<MonitoringRun[]> {
  const { data } = await apiClient.get("/monitoring/runs", { params: { limit } });
  return data;
}

export async function getEntityRiskScores(params?: {
  tier?: string;
  limit?: number;
}): Promise<EntityRiskScore[]> {
  const { data } = await apiClient.get("/monitoring/risk/entities", { params });
  return data;
}
