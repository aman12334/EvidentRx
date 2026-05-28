"use client";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/Card";
import { StatusChip } from "./StatusChip";
import { SeverityBadge } from "./SeverityBadge";
import type { InvestigationCase, Severity } from "@/lib/types/investigation";

interface CaseCardProps {
  case_: InvestigationCase;
}

export function CaseCard({ case_: c }: CaseCardProps) {
  const router = useRouter();

  return (
    <Card
      padding="sm"
      className="cursor-pointer transition-shadow hover:shadow-md"
      onClick={() => router.push(`/investigations/${c.case_id}`)}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2">
            <span className="font-mono text-xs text-slate-500">{c.case_number}</span>
            <StatusChip status={c.status} />
          </div>
          <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">
            {c.entity_name}
          </p>
          <p className="text-xs text-slate-500">{c.violation_category}</p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <SeverityBadge severity={c.priority as Severity} />
          {c.financial_exposure > 0 && (
            <span className="text-xs font-medium text-slate-700">
              ${c.financial_exposure.toLocaleString()}
            </span>
          )}
        </div>
      </div>
      <div className="mt-3 flex items-center gap-4 text-xs text-slate-500">
        <span>{c.total_findings} findings</span>
        {c.critical_findings > 0 && (
          <span className="text-red-600 font-medium">{c.critical_findings} critical</span>
        )}
        {c.composite_score != null && (
          <span>risk score: {c.composite_score.toFixed(3)}</span>
        )}
      </div>
    </Card>
  );
}
