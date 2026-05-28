"use client";
import { PageHeader }      from "@/components/layout/PageHeader";
import { RiskScoreCard }   from "@/components/intelligence/RiskScoreCard";
import { Spinner }         from "@/components/ui/Spinner";
import { EmptyState }      from "@/components/ui/EmptyState";
import { useEntityRiskScores } from "@/lib/hooks/useMonitoring";

export default function RiskScoresPage() {
  const { data: scores, isLoading, isError } = useEntityRiskScores();

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        title="Risk scores unavailable"
        description="Could not load entity risk scores. Ensure the monitoring pipeline has executed."
      />
    );
  }

  const sorted = [...(scores ?? [])].sort((a, b) => b.composite_score - a.composite_score);

  const critical = sorted.filter((s) => s.risk_tier === "critical");
  const high     = sorted.filter((s) => s.risk_tier === "high");
  const other    = sorted.filter((s) => s.risk_tier !== "critical" && s.risk_tier !== "high");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Entity Risk Scores"
        description="Composite risk scoring across all monitored covered entities and contract pharmacies."
      />

      {sorted.length === 0 ? (
        <EmptyState
          title="No risk scores"
          description="Run the monitoring engine to generate entity risk scores."
        />
      ) : (
        <>
          {critical.length > 0 && (
            <Section title="Critical Risk" count={critical.length}>
              {critical.map((s) => <RiskScoreCard key={`${s.entity_id}-${s.score_date}`} score={s} />)}
            </Section>
          )}
          {high.length > 0 && (
            <Section title="High Risk" count={high.length}>
              {high.map((s) => <RiskScoreCard key={`${s.entity_id}-${s.score_date}`} score={s} />)}
            </Section>
          )}
          {other.length > 0 && (
            <Section title="Medium / Low Risk" count={other.length}>
              {other.map((s) => <RiskScoreCard key={`${s.entity_id}-${s.score_date}`} score={s} />)}
            </Section>
          )}
        </>
      )}
    </div>
  );
}

interface SectionProps {
  title:    string;
  count:    number;
  children: React.ReactNode;
}

function Section({ title, count, children }: SectionProps) {
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">{title}</h3>
        <span className="text-xs text-slate-400">({count})</span>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {children}
      </div>
    </div>
  );
}
