/**
 * entities.ts — API client for covered entity endpoints.
 */
import { apiClient } from "./client";

export interface CoveredEntity {
  ce_id:             string;
  hrsa_id:           string;
  entity_name:       string;
  entity_type_code:  string | null;
  entity_type_description: string | null;
  city:              string | null;
  state_code:        string | null;
  zip_code:          string | null;
  npi:               string | null;
  primary_340b_program: string | null;
  program_status:    string;
  program_participation_start: string | null;
  is_active:         boolean;
}

export interface EntitySummary {
  ce_id:           string;
  entity_name:     string;
  open_cases:      number;
  total_findings:  number;
  critical_findings: number;
  total_exposure:  number | null;
  avg_risk_score:  number | null;
}

export interface EntityListResponse {
  entities: CoveredEntity[];
  total:    number;
  page:     number;
  limit:    number;
}

export interface EntityListParams {
  search?:      string;
  state_code?:  string;
  entity_type?: string;
  active_only?: boolean;
  page?:        number;
  limit?:       number;
}

export async function fetchEntities(params: EntityListParams = {}): Promise<EntityListResponse> {
  const { data } = await apiClient.get<EntityListResponse>("/entities", { params });
  return data;
}

export async function fetchEntity(ceId: string): Promise<CoveredEntity> {
  const { data } = await apiClient.get<CoveredEntity>(`/entities/${ceId}`);
  return data;
}

export async function fetchEntitySummary(ceId: string): Promise<EntitySummary> {
  const { data } = await apiClient.get<EntitySummary>(`/entities/${ceId}/summary`);
  return data;
}
