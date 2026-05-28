import { Card } from "@/components/ui/Card";
import type { DashboardMetrics } from "@/lib/types/investigation";

interface MetricTileProps {
  label:     string;
  value:     number | string;
  sublabel?: string;
  urgent?:   boolean;
}

function MetricTile({ label, value, sublabel, urgent }: MetricTileProps) {
  return (
    <Card padding="md">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-3xl font-bold ${urgent ? "text-red-600" : "text-slate-900 dark:text-white"}`}>
        {value}
      </p>
      {sublabel && <p className="mt-0.5 text-xs text-slate-400">{sublabel}</p>}
    </Card>
  );
}

export function MetricsGrid({ metrics }: { metrics: DashboardMetrics }) {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <MetricTile
        label="Open Cases"
        value={metrics.open_cases}
        sublabel={`${metrics.triaged_cases} triaged`}
      />
      <MetricTile
        label="Escalated"
        value={metrics.escalated_cases}
        urgent={metrics.escalated_cases > 0}
        sublabel={`${metrics.investigating_cases} investigating`}
      />
      <MetricTile
        label="Total Findings"
        value={metrics.total_findings.toLocaleString()}
        sublabel={`${metrics.critical_findings} critical`}
        urgent={metrics.critical_findings > 0}
      />
      <MetricTile
        label="Est. Exposure"
        value={`$${(metrics.total_exposure / 1000).toFixed(0)}k`}
        sublabel="financial risk"
      />
    </div>
  );
}
