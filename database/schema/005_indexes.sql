-- =============================================================================
-- Script 005: Indexes
--
-- Strategy:
--   - Unique partial indexes for SCD Type 2 current-row enforcement
--   - Composite indexes on the most frequent join / filter patterns
--   - GIN trigram indexes for name search (requires pg_trgm)
--   - Covering indexes (INCLUDE) where beneficial to avoid heap fetches
--   - Partial indexes where the filtered subset is small and hot
-- =============================================================================

-- ---------------------------------------------------------------------------
-- meta.ingestion_batches
-- ---------------------------------------------------------------------------
CREATE INDEX ix_ingestion_status
    ON meta.ingestion_batches (status)
    WHERE status IN ('pending', 'processing');  -- only care about incomplete batches

CREATE INDEX ix_ingestion_source_type
    ON meta.ingestion_batches (source_type, created_at DESC);


-- ---------------------------------------------------------------------------
-- ref.covered_entities
-- ---------------------------------------------------------------------------

-- Enforce one current row per hrsa_id (SCD Type 2)
CREATE UNIQUE INDEX ux_ce_hrsa_id_current
    ON ref.covered_entities (hrsa_id)
    WHERE is_current = TRUE;

CREATE INDEX ix_ce_hrsa_id
    ON ref.covered_entities (hrsa_id);                             -- history lookups

CREATE INDEX ix_ce_npi
    ON ref.covered_entities (npi)
    WHERE npi IS NOT NULL;

CREATE INDEX ix_ce_state_active
    ON ref.covered_entities (state_code, is_active)
    WHERE is_current = TRUE;

CREATE INDEX ix_ce_name_trgm
    ON ref.covered_entities USING gin (entity_name gin_trgm_ops); -- name search


-- ---------------------------------------------------------------------------
-- ref.contract_pharmacies
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX ux_cp_npi_hrsa_current
    ON ref.contract_pharmacies (pharmacy_npi, hrsa_id)
    WHERE is_current = TRUE AND pharmacy_npi IS NOT NULL;

CREATE INDEX ix_cp_ce_id
    ON ref.contract_pharmacies (covered_entity_id);

CREATE INDEX ix_cp_hrsa_id
    ON ref.contract_pharmacies (hrsa_id);

CREATE INDEX ix_cp_npi
    ON ref.contract_pharmacies (pharmacy_npi)
    WHERE pharmacy_npi IS NOT NULL;

CREATE INDEX ix_cp_active_by_ce
    ON ref.contract_pharmacies (covered_entity_id, is_active)
    WHERE is_current = TRUE;

CREATE INDEX ix_cp_state
    ON ref.contract_pharmacies (state_code);


-- ---------------------------------------------------------------------------
-- ref.medicaid_exclusions
-- ---------------------------------------------------------------------------

-- Core lookup: "was CE X carved in/out during period Y?"
CREATE INDEX ix_me_ce_period
    ON ref.medicaid_exclusions (covered_entity_id, period_start, period_end);

CREATE INDEX ix_me_hrsa_id
    ON ref.medicaid_exclusions (hrsa_id);

CREATE INDEX ix_me_state_period
    ON ref.medicaid_exclusions (state_code, period_start, period_end);

CREATE INDEX ix_me_filing_period
    ON ref.medicaid_exclusions (filing_period);

-- Partial index for current-period lookups
CREATE INDEX ix_me_current
    ON ref.medicaid_exclusions (covered_entity_id, exclusion_type)
    WHERE is_current = TRUE;


-- ---------------------------------------------------------------------------
-- ref.providers
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX ux_providers_npi_current
    ON ref.providers (npi)
    WHERE is_current = TRUE;

CREATE INDEX ix_providers_npi
    ON ref.providers (npi);    -- history lookups

CREATE INDEX ix_providers_state_type
    ON ref.providers (state_code, entity_type_code)
    WHERE is_current = TRUE;

CREATE INDEX ix_providers_org_name_trgm
    ON ref.providers USING gin (organization_name gin_trgm_ops)
    WHERE organization_name IS NOT NULL;


-- ---------------------------------------------------------------------------
-- ref.provider_taxonomies
-- ---------------------------------------------------------------------------

CREATE INDEX ix_pt_provider_id
    ON ref.provider_taxonomies (provider_id);

CREATE INDEX ix_pt_taxonomy_code
    ON ref.provider_taxonomies (taxonomy_code);

CREATE INDEX ix_pt_primary
    ON ref.provider_taxonomies (provider_id)
    WHERE is_primary = TRUE;


-- ---------------------------------------------------------------------------
-- ref.ndc_drugs
-- ---------------------------------------------------------------------------
-- ndc_11 UNIQUE is already created as a table constraint (enforces uniqueness + index)

CREATE INDEX ix_ndc_labeler
    ON ref.ndc_drugs (labeler_code);

CREATE INDEX ix_ndc_nonproprietary_trgm
    ON ref.ndc_drugs USING gin (nonproprietary_name gin_trgm_ops)
    WHERE nonproprietary_name IS NOT NULL;

CREATE INDEX ix_ndc_active
    ON ref.ndc_drugs (is_active, dea_schedule)
    WHERE is_active = TRUE;


-- ---------------------------------------------------------------------------
-- ops.purchases  (partitioned — indexes apply to all partitions automatically)
-- ---------------------------------------------------------------------------

CREATE INDEX ix_purchases_ce_date
    ON ops.purchases (covered_entity_id, purchase_date DESC);

CREATE INDEX ix_purchases_ndc
    ON ops.purchases (ndc_11, purchase_date DESC);

CREATE INDEX ix_purchases_340b
    ON ops.purchases (covered_entity_id, purchase_date)
    WHERE is_340b_purchase = TRUE;

CREATE INDEX ix_purchases_batch
    ON ops.purchases (batch_id);

CREATE INDEX ix_purchases_cp
    ON ops.purchases (contract_pharmacy_id)
    WHERE contract_pharmacy_id IS NOT NULL;


-- ---------------------------------------------------------------------------
-- ops.dispenses  (partitioned)
-- ---------------------------------------------------------------------------

CREATE INDEX ix_dispenses_ce_date
    ON ops.dispenses (covered_entity_id, dispense_date DESC);

CREATE INDEX ix_dispenses_ndc
    ON ops.dispenses (ndc_11, dispense_date DESC);

-- Patient-level lookups (for duplicate discount checks)
CREATE INDEX ix_dispenses_patient_date
    ON ops.dispenses (patient_id_hash, dispense_date DESC);

CREATE INDEX ix_dispenses_patient_ce
    ON ops.dispenses (covered_entity_id, patient_id_hash, dispense_date);

CREATE INDEX ix_dispenses_cp
    ON ops.dispenses (contract_pharmacy_id, dispense_date)
    WHERE contract_pharmacy_id IS NOT NULL;

CREATE INDEX ix_dispenses_medicaid
    ON ops.dispenses (covered_entity_id, dispense_date)
    WHERE payer_type = 'medicaid';

CREATE INDEX ix_dispenses_340b
    ON ops.dispenses (covered_entity_id, dispense_date)
    WHERE is_340b_dispense = TRUE;

CREATE INDEX ix_dispenses_batch
    ON ops.dispenses (batch_id);


-- ---------------------------------------------------------------------------
-- ops.claims  (partitioned)
-- ---------------------------------------------------------------------------

CREATE INDEX ix_claims_ce_date
    ON ops.claims (covered_entity_id, service_date DESC);

CREATE INDEX ix_claims_ndc
    ON ops.claims (ndc_11, service_date DESC);

-- Medicaid claim lookup — most frequent compliance check join
CREATE INDEX ix_claims_medicaid
    ON ops.claims (covered_entity_id, service_date)
    WHERE is_medicaid = TRUE;

CREATE INDEX ix_claims_340b_billed
    ON ops.claims (covered_entity_id, service_date)
    WHERE is_340b_billed = TRUE;

CREATE INDEX ix_claims_patient_date
    ON ops.claims (patient_id_hash, service_date DESC)
    WHERE patient_id_hash IS NOT NULL;

CREATE INDEX ix_claims_dispense_link
    ON ops.claims (dispense_id)
    WHERE dispense_id IS NOT NULL;

CREATE INDEX ix_claims_batch
    ON ops.claims (batch_id);


-- ---------------------------------------------------------------------------
-- ops.split_billing
-- ---------------------------------------------------------------------------

CREATE INDEX ix_sb_ce_date
    ON ops.split_billing (covered_entity_id, service_date DESC);

CREATE INDEX ix_sb_ndc
    ON ops.split_billing (ndc_11, service_date DESC);

CREATE INDEX ix_sb_patient
    ON ops.split_billing (patient_id_hash, service_date)
    WHERE patient_id_hash IS NOT NULL;

-- Risk flag indexes — used by rules engine to pull high-risk records
CREATE INDEX ix_sb_dup_discount_risk
    ON ops.split_billing (covered_entity_id, service_date)
    WHERE duplicate_discount_risk = TRUE;

CREATE INDEX ix_sb_medicaid_overlap
    ON ops.split_billing (covered_entity_id, service_date)
    WHERE medicaid_overlap_risk = TRUE;

CREATE INDEX ix_sb_any_risk
    ON ops.split_billing (covered_entity_id, risk_score DESC)
    WHERE risk_score > 0;

-- Links to specific partitioned records
CREATE INDEX ix_sb_purchase_link
    ON ops.split_billing (purchase_id, purchase_date);

CREATE INDEX ix_sb_dispense_link
    ON ops.split_billing (dispense_id, dispense_date);

CREATE INDEX ix_sb_claim_link
    ON ops.split_billing (claim_id, claim_service_date);

CREATE INDEX ix_sb_batch
    ON ops.split_billing (batch_id);


-- ---------------------------------------------------------------------------
-- audit.compliance_rules
-- ---------------------------------------------------------------------------

CREATE INDEX ix_rules_category_active
    ON audit.compliance_rules (rule_category, is_active)
    WHERE is_active = TRUE;

CREATE INDEX ix_rules_severity
    ON audit.compliance_rules (severity, is_active);

CREATE INDEX ix_rules_effective
    ON audit.compliance_rules (effective_date, expiration_date);


-- ---------------------------------------------------------------------------
-- audit.investigation_cases
-- ---------------------------------------------------------------------------

CREATE INDEX ix_cases_ce_id
    ON audit.investigation_cases (covered_entity_id);

CREATE INDEX ix_cases_status_priority
    ON audit.investigation_cases (status, priority)
    WHERE status NOT IN ('closed', 'dismissed');

CREATE INDEX ix_cases_assigned
    ON audit.investigation_cases (assigned_to)
    WHERE assigned_to IS NOT NULL;

CREATE INDEX ix_cases_workflow
    ON audit.investigation_cases (agent_workflow_id)
    WHERE agent_workflow_id IS NOT NULL;

CREATE INDEX ix_cases_due
    ON audit.investigation_cases (due_date)
    WHERE due_date IS NOT NULL AND status NOT IN ('closed', 'dismissed');


-- ---------------------------------------------------------------------------
-- audit.audit_findings
-- ---------------------------------------------------------------------------

CREATE INDEX ix_findings_ce_status
    ON audit.audit_findings (covered_entity_id, status);

CREATE INDEX ix_findings_rule
    ON audit.audit_findings (rule_id, detected_at DESC);

CREATE INDEX ix_findings_case
    ON audit.audit_findings (investigation_case_id)
    WHERE investigation_case_id IS NOT NULL;

CREATE INDEX ix_findings_type_severity
    ON audit.audit_findings (finding_type, severity, status);

CREATE INDEX ix_findings_open_by_ce
    ON audit.audit_findings (covered_entity_id, severity, detected_at DESC)
    WHERE status = 'open';

CREATE INDEX ix_findings_period
    ON audit.audit_findings (violation_period_start, violation_period_end);

CREATE INDEX ix_findings_sb_link
    ON audit.audit_findings (split_billing_id)
    WHERE split_billing_id IS NOT NULL;

-- JSONB GIN index for evidence_payload queries
CREATE INDEX ix_findings_evidence_gin
    ON audit.audit_findings USING gin (evidence_payload);


-- ---------------------------------------------------------------------------
-- audit.reasoning_traces
-- ---------------------------------------------------------------------------

CREATE INDEX ix_traces_session
    ON audit.reasoning_traces (session_id, workflow_step_sequence);

CREATE INDEX ix_traces_case
    ON audit.reasoning_traces (investigation_case_id, created_at DESC)
    WHERE investigation_case_id IS NOT NULL;

CREATE INDEX ix_traces_finding
    ON audit.reasoning_traces (finding_id, created_at DESC)
    WHERE finding_id IS NOT NULL;

CREATE INDEX ix_traces_human_review
    ON audit.reasoning_traces (human_review_required, created_at DESC)
    WHERE human_review_required = TRUE;

CREATE INDEX ix_traces_model
    ON audit.reasoning_traces (model_id, created_at DESC);
