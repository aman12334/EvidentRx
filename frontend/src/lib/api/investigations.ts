import type {
  DashboardMetrics,
  InvestigationCase,
  InvestigationCaseDetail,
  PaginatedResponse,
} from "@/lib/types/investigation";
import { apiClient } from "./client";

export async function getDashboardMetrics(): Promise<DashboardMetrics> {
  const { data } = await apiClient.get("/investigations/dashboard");
  return data;
}

export async function getInvestigationQueue(params?: {
  status?: string;
  priority?: string;
  page?: number;
  limit?: number;
}): Promise<PaginatedResponse<InvestigationCase>> {
  const { data } = await apiClient.get("/investigations/queue", { params });
  return data;
}

export async function getCaseDetail(
  caseId: string
): Promise<InvestigationCaseDetail> {
  const { data } = await apiClient.get(`/investigations/${caseId}`);
  return data;
}

export async function updateCaseStatus(
  caseId: string,
  status: string,
  resolutionNotes?: string
): Promise<{ updated: boolean }> {
  const { data } = await apiClient.patch(
    `/investigations/${caseId}/status`,
    { status, resolution_notes: resolutionNotes }
  );
  return data;
}
