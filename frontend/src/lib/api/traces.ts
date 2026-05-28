import type { WorkflowTrace } from "@/lib/types/trace";
import { apiClient } from "./client";

export async function getWorkflowTrace(caseId: string): Promise<WorkflowTrace> {
  const { data } = await apiClient.get(`/traces/case/${caseId}`);
  return data;
}
