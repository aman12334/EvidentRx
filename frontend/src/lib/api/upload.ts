/**
 * upload.ts — API client for the data upload pipeline.
 *
 * Wraps POST /api/v1/upload/claims, GET /api/v1/upload/history,
 * and GET /api/v1/upload/template.
 *
 * Uses fetch (not axios) for the multipart file upload so we can
 * track upload progress via XHR if needed in the future.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface FindingSummary {
  rule_code:   string;
  description: string;
  count:       number;
  severity:    "critical" | "high" | "medium" | "low";
}

export interface UploadResult {
  upload_id:          string;
  batch_id:           string;
  status:             "complete" | "partial" | "no_findings";
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
  status:         string;
  record_count:   number;
  started_at:     string;
  completed_at:   string | null;
  findings_count: number | null;
}

// ── API functions ──────────────────────────────────────────────────────────────

/**
 * Upload a CSV file and run the full 340B compliance pipeline.
 * @param file       - The CSV file to upload.
 * @param ceId       - Optional covered entity UUID to scope the upload.
 */
export async function uploadClaimsFile(
  file: File,
  ceId?: string,
): Promise<UploadResult> {
  const body = new FormData();
  body.append("file", file);
  if (ceId) body.append("covered_entity_id", ceId);

  const resp = await fetch(`${BASE}/upload/claims`, {
    method: "POST",
    body,
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail ?? `Upload failed: ${resp.status}`);
  }

  return resp.json() as Promise<UploadResult>;
}

/**
 * Fetch the 20 most recent upload batches for display in the history tab.
 */
export async function fetchUploadHistory(limit = 20): Promise<BatchHistoryItem[]> {
  const resp = await fetch(`${BASE}/upload/history?limit=${limit}`);
  if (!resp.ok) return [];
  return resp.json() as Promise<BatchHistoryItem[]>;
}

/**
 * Get the URL for downloading a blank CSV template.
 * @param fileType - "dispenses" or "claims"
 */
export function getTemplateDownloadUrl(fileType: "dispenses" | "claims" = "dispenses"): string {
  return `${BASE}/upload/template?file_type=${fileType}`;
}

/**
 * Validate a File object before uploading.
 * Returns an error string or null if valid.
 */
export function validateUploadFile(file: File): string | null {
  const MAX_BYTES = 20 * 1024 * 1024;  // 20 MB
  const ext = file.name.split(".").pop()?.toLowerCase();

  if (!ext || !["csv", "tsv", "txt"].includes(ext)) {
    return `Unsupported file type ".${ext ?? "?"}". Please upload a CSV file.`;
  }
  if (file.size === 0) {
    return "The selected file is empty.";
  }
  if (file.size > MAX_BYTES) {
    return `File size (${(file.size / 1024 / 1024).toFixed(1)} MB) exceeds the 20 MB limit.`;
  }
  return null;
}
