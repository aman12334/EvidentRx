import { useQuery } from "@tanstack/react-query";
import { getFindingDetail, getFindingsByRule, getFindingsForCase } from "@/lib/api/findings";

export function useCaseFindings(caseId: string, severity?: string, rulCode?: string, page = 1) {
  return useQuery({
    queryKey: ["findings", caseId, severity, rulCode, page],
    queryFn:  () => getFindingsForCase(caseId, { severity, rule_code: rulCode, page, limit: 50 }),
    enabled:  !!caseId,
    staleTime: 60_000,
  });
}

export function useFindingsByRule(caseId: string) {
  return useQuery({
    queryKey: ["findings-by-rule", caseId],
    queryFn:  () => getFindingsByRule(caseId),
    enabled:  !!caseId,
    staleTime: 120_000,
  });
}

export function useFindingDetail(findingId: string | null) {
  return useQuery({
    queryKey: ["finding", findingId],
    queryFn:  () => getFindingDetail(findingId!),
    enabled:  !!findingId,
    staleTime: 120_000,
  });
}
