import { useRouter } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { SeverityBadge } from "@/components/investigation/SeverityBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import type { InvestigationCase, Severity } from "@/lib/types/investigation";

interface EscalationQueueProps {
  escalations: InvestigationCase[];
}

export function EscalationQueue({ escalations }: EscalationQueueProps) {
  const router = useRouter();

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>
          <span className="flex items-center gap-2">
            Recent Escalations
            {escalations.length > 0 && (
              <span className="inline-flex items-center rounded-full bg-red-100 px-2 py-0.5 text-xs font-bold text-red-700">
                {escalations.length}
              </span>
            )}
          </span>
        </CardTitle>
      </CardHeader>

      {escalations.length === 0 ? (
        <div className="mt-3">
          <EmptyState
            title="No escalations"
            description="All cases are within normal thresholds."
          />
        </div>
      ) : (
        <ul className="mt-3 flex flex-col divide-y divide-slate-100 dark:divide-slate-800">
          {escalations.map((c) => (
            <li
              key={c.case_id}
              className="flex cursor-pointer items-start justify-between gap-3 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/50 -mx-1 px-1 rounded"
              onClick={() => router.push(`/investigations/${c.case_id}`)}
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">
                  {c.entity_name}
                </p>
                <p className="text-xs text-slate-500">
                  {c.case_number} · {c.violation_category}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <SeverityBadge severity={c.priority as Severity} />
                {c.financial_exposure > 0 && (
                  <span className="text-xs font-medium text-red-600">
                    ${c.financial_exposure.toLocaleString()}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
