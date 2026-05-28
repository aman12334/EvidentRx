"use client";
/**
 * FinancialExposureCard — displays total estimated financial exposure
 * across open investigation cases with trend indicator.
 */
import { Card } from "@/components/ui/Card";

interface Props {
  totalExposure:    number;       // USD
  criticalExposure: number;       // USD (critical-severity cases only)
  caseCount:        number;
  changePercent?:   number;       // vs previous period (positive = increase)
}

function formatDollar(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `$${(n / 1_000).toFixed(0)}k`;
  return `$${n.toFixed(0)}`;
}

export function FinancialExposureCard({
  totalExposure, criticalExposure, caseCount, changePercent,
}: Props) {
  const isIncreasing = (changePercent ?? 0) > 0;

  return (
    <Card padding="md">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Est. Financial Exposure
          </p>
          <p className="mt-1 text-3xl font-bold text-slate-900 dark:text-white">
            {formatDollar(totalExposure)}
          </p>
          <p className="text-xs text-slate-400 mt-0.5">
            across {caseCount} open case{caseCount !== 1 ? "s" : ""}
          </p>
        </div>

        {changePercent !== undefined && (
          <div className={`flex items-center gap-1 text-xs font-semibold rounded px-2 py-1 ${
            isIncreasing
              ? "text-red-700 bg-red-100 dark:bg-red-900/30"
              : "text-green-700 bg-green-100 dark:bg-green-900/30"
          }`}>
            <span>{isIncreasing ? "↑" : "↓"}</span>
            <span>{Math.abs(changePercent).toFixed(0)}%</span>
          </div>
        )}
      </div>

      {criticalExposure > 0 && (
        <div className="mt-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-100 px-3 py-2">
          <p className="text-xs text-red-700 dark:text-red-300">
            <span className="font-bold">{formatDollar(criticalExposure)}</span>
            {" "}from critical-severity cases — review immediately.
          </p>
        </div>
      )}
    </Card>
  );
}
