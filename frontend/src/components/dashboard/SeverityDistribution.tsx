import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import type { DashboardMetrics } from "@/lib/types/investigation";

const SEVERITY_CONFIG = [
  { key: "critical", label: "Critical", color: "bg-red-600",    text: "text-red-700 dark:text-red-400" },
  { key: "high",     label: "High",     color: "bg-orange-500", text: "text-orange-700 dark:text-orange-400" },
  { key: "medium",   label: "Medium",   color: "bg-yellow-500", text: "text-yellow-700 dark:text-yellow-400" },
  { key: "low",      label: "Low",      color: "bg-green-500",  text: "text-green-700 dark:text-green-400" },
] as const;

interface SeverityDistributionProps {
  severity: DashboardMetrics["severity"];
}

export function SeverityDistribution({ severity }: SeverityDistributionProps) {
  const total = severity.total || 1; // avoid divide-by-zero

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Severity Distribution</CardTitle>
      </CardHeader>

      {/* Stacked bar */}
      <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        {SEVERITY_CONFIG.map(({ key, color }) => {
          const pct = (severity[key] / total) * 100;
          if (pct === 0) return null;
          return (
            <div
              key={key}
              className={`${color} transition-all`}
              style={{ width: `${pct}%` }}
              title={`${key}: ${severity[key]}`}
            />
          );
        })}
      </div>

      {/* Legend */}
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
        {SEVERITY_CONFIG.map(({ key, label, color, text }) => {
          const count = severity[key];
          const pct   = total > 1 ? ((count / total) * 100).toFixed(0) : "0";
          return (
            <div key={key} className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <span className={`inline-block h-2.5 w-2.5 rounded-sm ${color}`} />
                <span className="text-xs text-slate-600 dark:text-slate-400">{label}</span>
              </div>
              <div className="flex items-baseline gap-1">
                <span className={`text-sm font-bold ${text}`}>{count}</span>
                <span className="text-xs text-slate-400">({pct}%)</span>
              </div>
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-right text-xs text-slate-400">
        {severity.total.toLocaleString()} total findings
      </p>
    </Card>
  );
}
