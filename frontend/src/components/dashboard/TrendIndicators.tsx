import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { useIntelligenceSummary } from "@/lib/hooks/useMonitoring";
import type { TrendRecord } from "@/lib/types/monitoring";

const DIRECTION_META: Record<string, { icon: string; color: string }> = {
  worsening:  { icon: "↑", color: "text-red-600" },
  improving:  { icon: "↓", color: "text-green-600" },
  stable:     { icon: "→", color: "text-slate-500" },
  volatile:   { icon: "⟳", color: "text-orange-500" },
};

function TrendRow({ trend }: { trend: TrendRecord }) {
  const meta = DIRECTION_META[trend.trend_direction] ?? DIRECTION_META.stable;
  return (
    <div className="flex items-center justify-between py-2 border-b border-slate-100 dark:border-slate-800 last:border-0">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold text-slate-800 dark:text-slate-200 truncate">
          {trend.entity_id}
        </p>
        <p className="text-xs text-slate-500">
          Rule {trend.rule_code} · {trend.window_type}
        </p>
      </div>
      <div className="flex items-center gap-3 shrink-0 text-right">
        <div>
          <p className="text-xs text-slate-500">velocity</p>
          <p className="text-sm font-bold text-slate-900 dark:text-white">
            {trend.velocity > 0 ? "+" : ""}
            {trend.velocity.toFixed(2)}/d
          </p>
        </div>
        <span className={`text-lg font-bold ${meta.color}`} title={trend.trend_direction}>
          {meta.icon}
        </span>
      </div>
    </div>
  );
}

export function TrendIndicators() {
  const { data, isLoading, isError } = useIntelligenceSummary();

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Worsening Trends</CardTitle>
      </CardHeader>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner size="md" />
        </div>
      ) : isError ? (
        <div className="mt-3">
          <EmptyState title="Unavailable" description="Intelligence summary could not be loaded." />
        </div>
      ) : !data || data.worsening_trends.length === 0 ? (
        <div className="mt-3">
          <EmptyState title="No worsening trends" description="All monitored entities are stable." />
        </div>
      ) : (
        <div className="mt-3">
          {data.worsening_trends.slice(0, 6).map((t, i) => (
            <TrendRow key={`${t.entity_id}-${t.rule_code}-${i}`} trend={t} />
          ))}
          {data.last_monitoring_run && (
            <p className="mt-2 text-right text-xs text-slate-400">
              Last run:{" "}
              {data.last_monitoring_run.completed_at
                ? new Date(data.last_monitoring_run.completed_at).toLocaleString()
                : "in progress"}
            </p>
          )}
        </div>
      )}
    </Card>
  );
}
