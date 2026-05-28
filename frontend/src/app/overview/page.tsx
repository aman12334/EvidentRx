"use client";
/**
 * /overview — Compliance Intelligence Dashboard
 *
 * Primary overview showing:
 *   • Key compliance KPIs (MetricsGrid)
 *   • Compliance risk score gauge
 *   • Financial exposure summary
 *   • Rule violation breakdown chart
 *   • Upload widget for quick data ingestion
 */
import { Suspense } from "react";
import { PageHeader }              from "@/components/layout/PageHeader";
import { MetricsGrid }             from "@/components/dashboard/MetricsGrid";
import { RuleViolationBreakdown }  from "@/components/dashboard/RuleViolationBreakdown";
import { FinancialExposureCard }   from "@/components/dashboard/FinancialExposureCard";
import { ComplianceScoreCard }     from "@/components/dashboard/ComplianceScoreCard";
import { UploadWidget }            from "@/components/dashboard/UploadWidget";
import { Spinner }                 from "@/components/ui/Spinner";
import { useDashboardSummary, useRuleBreakdown } from "@/lib/hooks/useDashboard";

function DashboardContent() {
  const { data: summary, loading: sLoading }     = useDashboardSummary();
  const { data: rules,   loading: rLoading }     = useRuleBreakdown(8);

  if (sLoading || rLoading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="text-center py-12 text-slate-400 text-sm">
        Could not load dashboard data. Is the API running?
      </div>
    );
  }

  const metricsForGrid = {
    open_cases:         summary.open_cases,
    escalated_cases:    summary.escalated_cases,
    triaged_cases:      summary.triaged_cases,
    investigating_cases: summary.investigating_cases,
    total_findings:     summary.total_findings,
    critical_findings:  summary.critical_findings,
    total_exposure:     summary.total_exposure ?? 0,
  };

  const ruleBreakdownData = (rules ?? []).map(r => ({
    rule_code:  r.rule_code,
    rule_name:  r.rule_name,
    count:      r.count,
    severity:   r.severity as "critical" | "high" | "medium" | "low",
  }));

  return (
    <div className="space-y-6">
      {/* KPI tiles */}
      <MetricsGrid metrics={metricsForGrid as any} />

      {/* Second row: risk score + exposure + upload */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <ComplianceScoreCard
          score={summary.avg_risk_score ?? 0}
          label="Avg. Risk Score"
          sublabel={`${summary.covered_entities} covered entities`}
        />
        <FinancialExposureCard
          totalExposure={summary.total_exposure ?? 0}
          criticalExposure={0}
          caseCount={summary.open_cases}
        />
        <UploadWidget />
      </div>

      {/* Third row: rule breakdown */}
      <RuleViolationBreakdown rules={ruleBreakdownData} maxBars={8} />

      {/* Weekly activity footer */}
      <div className="text-xs text-slate-400 flex gap-6">
        <span>
          <span className="font-semibold text-slate-600 dark:text-slate-300">
            {summary.findings_this_week}
          </span>{" "}
          new findings this week
        </span>
        <span>
          <span className="font-semibold text-slate-600 dark:text-slate-300">
            {summary.uploads_this_week}
          </span>{" "}
          data uploads this week
        </span>
      </div>
    </div>
  );
}

export default function OverviewPage() {
  return (
    <div>
      <PageHeader
        title="Compliance Overview"
        description="Live 340B compliance intelligence across all covered entities."
      />
      <Suspense fallback={<Spinner size="lg" />}>
        <DashboardContent />
      </Suspense>
    </div>
  );
}
