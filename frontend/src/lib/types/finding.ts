import type { Severity } from "./investigation";

export interface Finding {
  finding_id:        string;
  case_id:           string | null;
  finding_code:      string;
  rule_code:         string;
  severity:          Severity;
  covered_entity_id: string;
  entity_name:       string | null;
  evidence_payload:  Record<string, unknown>;
  created_at:        string | null;
}

export interface FindingDetail extends Finding {
  entity_references:  Record<string, unknown>;
  ndc_11:             string | null;
  pharmacy_id:        string | null;
  pharmacy_name:      string | null;
  financial_exposure: number;
}

export interface FindingsByRule {
  rule_code: string;
  count:     number;
  critical:  number;
  high:      number;
  medium:    number;
  low:       number;
  exposure:  number;
}
