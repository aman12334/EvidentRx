/**
 * dashboard.ts — API client for the compliance dashboard endpoints.
 *
 * Wraps GET /api/v1/dashboard/summary, /rule-breakdown, /risk-matrix,
 * and /exposure-trend.
 */
import { apiClient } from "./client";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface DashboardSummary {
  open_cases:          number;
  escalated_cases:     number;
  triaged_cases:       number;
  investigating_cases: number;
  total_findings:      number;
  critical_findings:   number;
  high_findings:       number;
  total_exposure:      number | null;
  avg_risk_score:      number | null;
  covered_entities:    number;
  uploads_this_week:   number;
  findings_this_week:  number;
}

export interface RuleBreakdownItem {
  rule_code: string;
  rule_name: string;
  severity:  string;
  count:     number;
  exposure:  number | null;
}

export interface RiskMatrixCell {
  status:   string;
  priority: string;
  count:    number;
}

export interface ExposureTrendPoint {
  date:     string;
  exposure: number;
  count:    number;
}

// ── API functions ──────────────────────────────────────────────────────────────

export async function fetchDashboardSummary(): Promise<DashboardSummary> {
  const { data } = await apiClient.get<DashboardSummary>("/dashboard/summary");
  return data;
}

export async function fetchRuleBreakdown(limit = 10): Promise<RuleBreakdownItem[]> {
  const { data } = await apiClient.get<RuleBreakdownItem[]>("/dashboard/rule-breakdown", {
    params: { limit },
  });
  return data;
}

export async function fetchRiskMatrix(): Promise<RiskMatrixCell[]> {
  const { data } = await apiClient.get<RiskMatrixCell[]>("/dashboard/risk-matrix");
  return data;
}

export async function fetchExposureTrend(days = 90): Promise<ExposureTrendPoint[]> {
  const { data } = await apiClient.get<ExposureTrendPoint[]>("/dashboard/exposure-trend", {
    params: { days },
  });
  return data;
}
