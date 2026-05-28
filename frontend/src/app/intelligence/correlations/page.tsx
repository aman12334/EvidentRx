"use client";
import { PageHeader }        from "@/components/layout/PageHeader";
import { CorrelationMatrix } from "@/components/intelligence/CorrelationMatrix";
import { Spinner }           from "@/components/ui/Spinner";
import { EmptyState }        from "@/components/ui/EmptyState";
import { useIntelligenceSummary } from "@/lib/hooks/useMonitoring";

export default function CorrelationsPage() {
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
        title="Correlations unavailable"
        description="Could not load cross-case correlation data."
      />
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Cross-Case Correlations"
        description="Statistically significant entity and pattern overlap between investigation cases."
      />

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard
          label="Total Correlations"
          value={data.high_correlations.length}
        />
        <StatCard
          label="Avg Strength"
          value={
            data.high_correlations.length > 0
              ? `${(
                  (data.high_correlations.reduce((s, c) => s + c.strength, 0) /
                    data.high_correlations.length) *
                  100
                ).toFixed(0)}%`
              : "—"
          }
        />
        <StatCard
          label="Distinct Types"
          value={
            new Set(data.high_correlations.map((c) => c.correlation_type)).size
          }
        />
      </div>

      <CorrelationMatrix correlations={data.high_correlations} />
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-4 py-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">{value}</p>
    </div>
  );
}
