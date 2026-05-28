export type CaseStatus =
  | "open"
  | "triaged"
  | "investigating"
  | "escalated"
  | "resolved"
  | "closed";

export type Priority = "critical" | "high" | "medium" | "low";
export type Severity = "critical" | "high" | "medium" | "low";
export type RiskTier = "critical" | "high" | "medium" | "low";

export interface InvestigationCase {
  case_id:            string;
  case_number:        string;
  status:             CaseStatus;
  priority:           Priority;
  violation_category: string;
  entity_name:        string;
  covered_entity_id:  string;
  risk_level:         RiskTier | null;
  composite_score:    number | null;
  total_findings:     number;
  critical_findings:  number;
  high_findings:      number;
  financial_exposure: number;
  opened_at:          string | null;
  assigned_to:        string | null;
}

export interface InvestigationCaseDetail extends InvestigationCase {
  medium_findings:  number;
  low_findings:     number;
  unique_patients:  number;
  ndc_list:         string[];
  findings_by_rule: Record<string, number>;
  closed_at:        string | null;
  resolution_notes: string | null;
}

export interface DashboardMetrics {
  open_cases:          number;
  escalated_cases:     number;
  triaged_cases:       number;
  investigating_cases: number;
  total_findings:      number;
  critical_findings:   number;
  total_exposure:      number;
  severity: {
    critical: number;
    high:     number;
    medium:   number;
    low:      number;
    total:    number;
  };
  recent_escalations: InvestigationCase[];
}

export interface PaginatedResponse<T> {
  total: number;
  page:  number;
  limit: number;
  items: T[];
}
