import type { RiskTier } from "./investigation";

export interface EntityRiskScore {
  entity_id:              string;
  entity_type:            string;
  score_date:             string;
  composite_score:        number;
  risk_tier:              RiskTier;
  finding_velocity:       number;
  exposure_trajectory:    number;
  escalation_probability: number;
  trend_direction:        string;
}

export interface TrendRecord {
  entity_id:          string;
  entity_type:        string;
  rule_code:          string;
  window_type:        string;
  finding_count:      number;
  critical_count:     number;
  risk_score:         number;
  trend_direction:    string;
  velocity:           number;
  acceleration:       number;
  prior_period_count: number;
}

export interface Correlation {
  case_id_a:        string;
  case_id_b:        string;
  correlation_type: string;
  strength:         number;
  explanation:      string;
  shared_entities:  Record<string, unknown>;
}

export interface DriftSignal {
  drift_type:    string;
  subject_id:    string;
  subject_label: string;
  magnitude:     string;
  direction:     string;
  change_pct:    number;
  explanation:   string;
}

export interface MonitoringRun {
  run_id:              string;
  run_type:            string;
  status:              string;
  findings_evaluated:  number;
  new_findings:        number;
  drifts_detected:     number;
  correlations_found:  number;
  started_at:          string | null;
  completed_at:        string | null;
}

export interface IntelligenceSummary {
  as_of:                  string;
  top_risk_entities:      EntityRiskScore[];
  worsening_trends:       TrendRecord[];
  high_correlations:      Correlation[];
  critical_drift_signals: DriftSignal[];
  last_monitoring_run:    MonitoringRun | null;
}
