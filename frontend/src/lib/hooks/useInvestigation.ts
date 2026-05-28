import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getCaseDetail,
  getDashboardMetrics,
  getInvestigationQueue,
  updateCaseStatus,
} from "@/lib/api/investigations";
import type { CaseStatus } from "@/lib/types/investigation";

export const QUERY_KEYS = {
  dashboard:   ["dashboard"] as const,
  queue:       (status?: string, page?: number) => ["queue", status, page] as const,
  caseDetail:  (id: string) => ["case", id] as const,
};

export function useDashboard() {
  return useQuery({
    queryKey: QUERY_KEYS.dashboard,
    queryFn:  getDashboardMetrics,
    staleTime: 60_000,
  });
}

interface QueueParams {
  status?: CaseStatus;
  page?:   number;
  limit?:  number;
}

export function useInvestigationQueue({ status, page = 1, limit = 25 }: QueueParams = {}) {
  return useQuery({
    queryKey: QUERY_KEYS.queue(status, page),
    queryFn:  () => getInvestigationQueue({ status, page, limit }),
    staleTime: 30_000,
  });
}

export function useCaseDetail(caseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.caseDetail(caseId),
    queryFn:  () => getCaseDetail(caseId),
    enabled:  Boolean(caseId),
    staleTime: 60_000,
  });
}

interface StatusUpdate {
  status:           string;
  resolution_notes?: string;
}

/**
 * useUpdateCaseStatus(caseId) — bound to a specific case.
 * Usage: const { mutate } = useUpdateCaseStatus(caseId)
 *        mutate({ status: "escalated", resolution_notes: "..." })
 */
export function useUpdateCaseStatus(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ status, resolution_notes }: StatusUpdate) =>
      updateCaseStatus(caseId, status, resolution_notes),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QUERY_KEYS.caseDetail(caseId) });
      qc.invalidateQueries({ queryKey: ["queue"] });
      qc.invalidateQueries({ queryKey: QUERY_KEYS.dashboard });
    },
  });
}
