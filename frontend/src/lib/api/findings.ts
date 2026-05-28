import type { Finding, FindingDetail, FindingsByRule } from "@/lib/types/finding";
import type { PaginatedResponse as PR } from "@/lib/types/investigation";
import { apiClient } from "./client";

export async function getFindingsForCase(
  caseId: string,
  params?: { severity?: string; rule_code?: string; page?: number; limit?: number }
): Promise<PR<Finding>> {
  const { data } = await apiClient.get(`/findings/case/${caseId}`, { params });
  return data;
}

export async function getFindingsByRule(
  caseId: string
): Promise<FindingsByRule[]> {
  const { data } = await apiClient.get(`/findings/case/${caseId}/by-rule`);
  return data;
}

export async function getFindingDetail(findingId: string): Promise<FindingDetail> {
  const { data } = await apiClient.get(`/findings/${findingId}`);
  return data;
}
