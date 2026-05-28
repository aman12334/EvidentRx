/**
 * upload.ts — TypeScript types for the 340B data upload pipeline.
 *
 * These mirror the Pydantic schemas in api/schemas/upload.py.
 */

export type UploadSeverity = "critical" | "high" | "medium" | "low";
export type UploadStatus   = "complete" | "partial" | "no_findings";
export type BatchStatus    = "processing" | "complete" | "failed" | "unknown";

export interface FindingSummary {
  rule_code:   string;
  description: string;
  count:       number;
  severity:    UploadSeverity;
}

export interface UploadResult {
  upload_id:          string;
  batch_id:           string;
  status:             UploadStatus;
  message:            string;
  rows_parsed:        number;
  dispenses_inserted: number;
  claims_inserted:    number;
  split_billing_rows: number;
  cases_created:      number;
  total_findings:     number;
  critical_findings:  number;
  high_findings:      number;
  estimated_exposure: number | null;
  findings_by_rule:   FindingSummary[];
  case_ids:           string[];
  processing_ms:      number;
}

export interface BatchHistoryItem {
  batch_id:       string;
  filename:       string;
  status:         BatchStatus;
  record_count:   number;
  started_at:     string;             // ISO-8601
  completed_at:   string | null;
  findings_count: number | null;
}

export interface ColumnMapping {
  source_column: string;
  target_field:  string;
  mapped:        boolean;
  sample_value?: string;
}

export interface UploadPreview {
  filename:          string;
  rows_detected:     number;
  file_type:         "dispenses" | "claims" | "mixed";
  column_mappings:   ColumnMapping[];
  unmapped_columns:  string[];
  sample_rows:       Record<string, string>[];
  warnings:          string[];
}

export interface UploadValidationError {
  error_code: string;
  detail:     string;
  row_number?: number;
  column?:     string;
}
