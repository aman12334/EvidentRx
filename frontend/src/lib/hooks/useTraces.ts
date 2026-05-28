import { useQuery } from "@tanstack/react-query";
import { getWorkflowTrace } from "@/lib/api/traces";

export function useWorkflowTrace(caseId: string | null) {
  return useQuery({
    queryKey: ["trace", caseId],
    queryFn:  () => getWorkflowTrace(caseId!),
    enabled:  !!caseId,
    staleTime: 120_000,
  });
}
