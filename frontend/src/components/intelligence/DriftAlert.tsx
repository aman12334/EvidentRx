import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import type { DriftSignal } from "@/lib/types/monitoring";

interface DriftAlertProps {
  signals: DriftSignal[];
}

const MAGNITUDE_META: Record<string, { color: string; bg: string; border: string; icon: string }> = {
  critical: {
    color:  "text-red-700 dark:text-red-400",
    bg:     "bg-red-50 dark:bg-red-950/20",
    border: "border-red-200 dark:border-red-800",
    icon:   "🔴",
  },
  high: {
    color:  "text-orange-700 dark:text-orange-400",
    bg:     "bg-orange-50 dark:bg-orange-950/20",
    border: "border-orange-200 dark:border-orange-800",
    icon:   "🟠",
  },
  medium: {
    color:  "text-yellow-700 dark:text-yellow-400",
    bg:     "bg-yellow-50 dark:bg-yellow-950/20",
    border: "border-yellow-200 dark:border-yellow-800",
    icon:   "🟡",
  },
  low: {
    color:  "text-slate-700 dark:text-slate-400",
    bg:     "bg-slate-50 dark:bg-slate-800/30",
    border: "border-slate-200 dark:border-slate-700",
    icon:   "⚪",
  },
};

export function DriftAlert({ signals }: DriftAlertProps) {
  if (signals.length === 0) {
    return (
      <Card padding="md">
        <CardHeader>
          <CardTitle>Drift Signals</CardTitle>
        </CardHeader>
        <div className="mt-3">
          <EmptyState
            title="No drift detected"
            description="Rule, entity, and model distributions are stable within configured thresholds."
          />
        </div>
      </Card>
    );
  }

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>
          <span className="flex items-center gap-2">
            Drift Signals
            <span className="inline-flex items-center rounded-full bg-red-100 dark:bg-red-900/50 px-2 py-0.5 text-xs font-bold text-red-700 dark:text-red-400">
              {signals.length}
            </span>
          </span>
        </CardTitle>
      </CardHeader>

      <div className="mt-3 flex flex-col gap-2.5">
        {signals.map((s, i) => {
          const meta = MAGNITUDE_META[s.magnitude] ?? MAGNITUDE_META.low;
          return (
            <div
              key={i}
              className={`rounded border px-3 py-2.5 ${meta.bg} ${meta.border}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-1.5">
                    <span>{meta.icon}</span>
                    <span className={`text-xs font-bold uppercase tracking-wide ${meta.color}`}>
                      {s.magnitude} drift
                    </span>
                    <span className="text-xs text-slate-400">·</span>
                    <span className="text-xs text-slate-500 capitalize">
                      {s.drift_type.replace(/_/g, " ")}
                    </span>
                  </div>
                  <p className="mt-0.5 text-sm font-semibold text-slate-800 dark:text-slate-200">
                    {s.subject_label}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className={`text-sm font-bold ${s.change_pct > 0 ? "text-red-600" : "text-green-600"}`}>
                    {s.change_pct > 0 ? "+" : ""}{s.change_pct.toFixed(1)}%
                  </p>
                  <p className="text-xs text-slate-400 capitalize">{s.direction}</p>
                </div>
              </div>

              {s.explanation && (
                <p className="mt-1.5 text-xs text-slate-600 dark:text-slate-400">{s.explanation}</p>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
