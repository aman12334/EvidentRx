import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import type { DashboardMetrics } from "@/lib/types/investigation";

interface ExposureSummaryProps {
  metrics: DashboardMetrics;
}

export function ExposureSummary({ metrics }: ExposureSummaryProps) {
  const exposure = metrics.total_exposure;

  function formatDollar(n: number): string {
    if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000)     return `$${(n / 1_000).toFixed(1)}k`;
    return `$${n.toFixed(0)}`;
  }

  // Rough per-severity breakdown assumption based on finding counts
  const total = metrics.severity.total || 1;
  const critPct = metrics.severity.critical / total;
  const highPct = metrics.severity.high     / total;

  const critExposure   = exposure * critPct;
  const highExposure   = exposure * highPct;
  const otherExposure  = exposure * (1 - critPct - highPct);

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Financial Exposure</CardTitle>
      </CardHeader>

      <p className="mt-2 text-3xl font-bold text-slate-900 dark:text-white">
        {formatDollar(exposure)}
      </p>
      <p className="text-xs text-slate-400">total estimated risk</p>

      <div className="mt-4 flex flex-col gap-2">
        <ExposureLine
          label="Critical findings"
          amount={critExposure}
          color="bg-red-500"
          total={exposure}
          format={formatDollar}
        />
        <ExposureLine
          label="High findings"
          amount={highExposure}
          color="bg-orange-400"
          total={exposure}
          format={formatDollar}
        />
        <ExposureLine
          label="Med / Low findings"
          amount={otherExposure}
          color="bg-slate-300 dark:bg-slate-600"
          total={exposure}
          format={formatDollar}
        />
      </div>

      <p className="mt-4 text-xs text-slate-400">
        Across {metrics.open_cases + metrics.escalated_cases} active cases
      </p>
    </Card>
  );
}

interface ExposureLineProps {
  label:  string;
  amount: number;
  color:  string;
  total:  number;
  format: (n: number) => string;
}

function ExposureLine({ label, amount, color, total, format }: ExposureLineProps) {
  const pct = total > 0 ? (amount / total) * 100 : 0;
  return (
    <div>
      <div className="mb-0.5 flex justify-between text-xs">
        <span className="text-slate-600 dark:text-slate-400">{label}</span>
        <span className="font-semibold text-slate-800 dark:text-slate-200">{format(amount)}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className={`h-full rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
