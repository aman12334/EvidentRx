"use client";
import { PageHeader }        from "@/components/layout/PageHeader";
import { TrendChart }        from "@/components/intelligence/TrendChart";
import { DriftAlert }        from "@/components/intelligence/DriftAlert";
import { CorrelationMatrix } from "@/components/intelligence/CorrelationMatrix";
import { Spinner }           from "@/components/ui/Spinner";
import { EmptyState }        from "@/components/ui/EmptyState";
import { useIntelligenceSummary } from "@/lib/hooks/useMonitoring";

export default function IntelligencePage() {
  const { data, isLoading, isError } = useIntelligenceSummary();

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <EmptyState
        title="Intelligence unavailable"
        description="Could not load the intelligence summary. Ensure the monitoring engine has run at least once."
      />
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Intelligence Summary"
        description={`As of ${new Date(data.as_of).toLocaleString()} · deterministic trend and risk analysis`}
      />

      {/* Last run status */}
      {data.last_monitoring_run && (
        <div className="flex items-center gap-3 rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-4 py-2.5 text-xs text-slate-600 dark:text-slate-400">
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              data.last_monitoring_run.status === "completed" ? "bg-green-500"
              : data.last_monitoring_run.status === "running"  ? "bg-blue-500 animate-pulse"
              : "bg-red-500"
            }`}
          />
          <span>
            Last run: <strong className="text-slate-800 dark:text-slate-200">{data.last_monitoring_run.status}</strong>
            {" "}·{" "}
            {data.last_monitoring_run.findings_evaluated.toLocaleString()} findings evaluated
            {" "}·{" "}
            {data.last_monitoring_run.drifts_detected} drift signals
            {" "}·{" "}
            {data.last_monitoring_run.correlations_found} correlations
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <TrendChart />
        <DriftAlert signals={data.critical_drift_signals} />
      </div>

      <CorrelationMatrix correlations={data.high_correlations} />
    </div>
  );
}
