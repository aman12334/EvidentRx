-- ============================================================================
-- 012_regulatory_tables.sql
-- Phase 13 — Regulatory Intelligence & Policy Automation Layer
-- ============================================================================
-- Schema: regulatory
--
-- Tables
-- ──────
-- regulatory.policy_domains             — controlled vocabulary for policy domains
-- regulatory.regulatory_document_families — document version family groups
-- regulatory.regulatory_documents       — ingested regulatory documents (versioned)
-- regulatory.ingestion_records          — raw ingestion audit log
-- regulatory.policy_sync_sources        — scheduled sync source registry
-- regulatory.policy_diffs               — diff results between document versions
-- regulatory.policy_changes             — individual change records within a diff
-- regulatory.drift_reports              — regulatory drift detection snapshots
-- regulatory.drift_findings             — individual drift findings
-- regulatory.impact_reports             — policy change impact assessments
-- regulatory.affected_elements          — elements affected by an impact report
-- regulatory.policy_recommendations     — governed recommendation objects
-- regulatory.recommendation_lineage     — immutable lifecycle audit trail
-- regulatory.policy_citations           — investigation ↔ regulatory doc links
-- regulatory.investigation_policy_contexts — per-investigation policy snapshots
-- regulatory.readiness_snapshots        — compliance readiness assessments
-- regulatory.readiness_signals          — individual scoring signals per snapshot
-- regulatory.timeline_events            — append-only regulatory event timeline
-- regulatory.activation_workflows       — policy activation governance workflows
-- regulatory.workflow_audit_entries     — immutable audit trail per workflow
-- regulatory.graph_nodes                — regulatory graph nodes
-- regulatory.graph_edges                — regulatory graph edges
-- regulatory.evaluation_scenarios       — evaluation harness scenario registry
-- regulatory.evaluation_results         — evaluation harness run results
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS regulatory;

-- ── Domains ───────────────────────────────────────────────────────────────────

CREATE TABLE regulatory.policy_domains (
    domain          VARCHAR(80)   PRIMARY KEY,
    label           VARCHAR(120)  NOT NULL,
    description     TEXT,
    required        BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ── Document families ─────────────────────────────────────────────────────────

CREATE TABLE regulatory.regulatory_document_families (
    family_id       VARCHAR(32)   PRIMARY KEY,   -- rfam_<hex16>
    canonical_title VARCHAR(500)  NOT NULL,
    source          VARCHAR(40)   NOT NULL,       -- DocumentSource enum value
    domains         TEXT[]        NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    metadata        JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reg_families_source
    ON regulatory.regulatory_document_families (source);

-- ── Regulatory documents ──────────────────────────────────────────────────────

CREATE TABLE regulatory.regulatory_documents (
    doc_id          VARCHAR(32)   PRIMARY KEY,   -- rdoc_<hex16>
    family_id       VARCHAR(32)   NOT NULL REFERENCES regulatory.regulatory_document_families (family_id),
    tenant_id       UUID          REFERENCES saas.tenants (tenant_id) ON DELETE SET NULL,
    title           VARCHAR(500)  NOT NULL,
    version         VARCHAR(40)   NOT NULL,
    source          VARCHAR(40)   NOT NULL,
    format          VARCHAR(20)   NOT NULL,
    status          VARCHAR(20)   NOT NULL DEFAULT 'pending',
    domains         TEXT[]        NOT NULL DEFAULT '{}',
    content_hash    CHAR(64)      NOT NULL,       -- SHA-256 of raw content
    raw_text        TEXT,
    summary         TEXT,
    word_count      INTEGER       NOT NULL DEFAULT 0,
    language        VARCHAR(10)   NOT NULL DEFAULT 'en',
    -- Attribution
    issuing_body    VARCHAR(200),
    source_url      VARCHAR(2000),
    effective_date  VARCHAR(20),                  -- ISO-8601 date string
    expiry_date     VARCHAR(20),
    publication_date VARCHAR(20),
    -- Timestamps
    ingested_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    indexed_at      TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,
    -- Constraints
    UNIQUE (family_id, version)
);

CREATE INDEX idx_reg_docs_family
    ON regulatory.regulatory_documents (family_id);
CREATE INDEX idx_reg_docs_status
    ON regulatory.regulatory_documents (status);
CREATE INDEX idx_reg_docs_tenant
    ON regulatory.regulatory_documents (tenant_id) WHERE tenant_id IS NOT NULL;
CREATE INDEX idx_reg_docs_domains
    ON regulatory.regulatory_documents USING GIN (domains);
CREATE INDEX idx_reg_docs_ingested
    ON regulatory.regulatory_documents (ingested_at);
CREATE UNIQUE INDEX idx_reg_docs_content_hash
    ON regulatory.regulatory_documents (content_hash);

-- ── Ingestion records ─────────────────────────────────────────────────────────

CREATE TABLE regulatory.ingestion_records (
    record_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id          VARCHAR(32)   NOT NULL REFERENCES regulatory.regulatory_documents (doc_id),
    tenant_id       UUID          REFERENCES saas.tenants (tenant_id) ON DELETE SET NULL,
    source_url      VARCHAR(2000),
    triggered_by    VARCHAR(120)  NOT NULL DEFAULT 'system',
    stages_completed TEXT[]       NOT NULL DEFAULT '{}',
    success         BOOLEAN       NOT NULL,
    error_message   TEXT,
    duration_ms     FLOAT,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_ingestion_doc
    ON regulatory.ingestion_records (doc_id);
CREATE INDEX idx_reg_ingestion_tenant
    ON regulatory.ingestion_records (tenant_id) WHERE tenant_id IS NOT NULL;

-- ── Policy sync sources ───────────────────────────────────────────────────────

CREATE TABLE regulatory.policy_sync_sources (
    source_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(200)  NOT NULL,
    source_type     VARCHAR(40)   NOT NULL,       -- DocumentSource enum
    base_url        VARCHAR(2000) NOT NULL,
    frequency       VARCHAR(20)   NOT NULL DEFAULT 'monthly',
    domains         TEXT[]        NOT NULL DEFAULT '{}',
    enabled         BOOLEAN       NOT NULL DEFAULT TRUE,
    last_synced_at  TIMESTAMPTZ,
    next_sync_at    TIMESTAMPTZ,
    consecutive_failures INTEGER  NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    metadata        JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reg_sync_sources_next
    ON regulatory.policy_sync_sources (next_sync_at) WHERE enabled = TRUE;

-- ── Policy diffs ──────────────────────────────────────────────────────────────

CREATE TABLE regulatory.policy_diffs (
    diff_id         VARCHAR(36)   PRIMARY KEY,    -- UUID string
    family_id       VARCHAR(32)   NOT NULL REFERENCES regulatory.regulatory_document_families (family_id),
    prior_doc_id    VARCHAR(32)   NOT NULL REFERENCES regulatory.regulatory_documents (doc_id),
    new_doc_id      VARCHAR(32)   NOT NULL REFERENCES regulatory.regulatory_documents (doc_id),
    prior_version   VARCHAR(40)   NOT NULL,
    new_version     VARCHAR(40)   NOT NULL,
    overall_severity VARCHAR(20)  NOT NULL,
    change_count    INTEGER       NOT NULL DEFAULT 0,
    jaccard_similarity FLOAT      NOT NULL DEFAULT 0.0,
    summary         TEXT,
    content_hash    CHAR(64)      NOT NULL,
    diffed_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    tenant_id       UUID          REFERENCES saas.tenants (tenant_id) ON DELETE SET NULL,
    UNIQUE (prior_doc_id, new_doc_id)
);

CREATE INDEX idx_reg_diffs_family
    ON regulatory.policy_diffs (family_id);
CREATE INDEX idx_reg_diffs_severity
    ON regulatory.policy_diffs (overall_severity);
CREATE INDEX idx_reg_diffs_diffed_at
    ON regulatory.policy_diffs (diffed_at);

-- ── Policy changes ────────────────────────────────────────────────────────────

CREATE TABLE regulatory.policy_changes (
    change_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    diff_id         VARCHAR(36)   NOT NULL REFERENCES regulatory.policy_diffs (diff_id),
    category        VARCHAR(20)   NOT NULL,       -- ChangeCategory enum
    severity        VARCHAR(20)   NOT NULL,       -- ChangeSeverity enum
    section         VARCHAR(200)  NOT NULL,
    description     TEXT          NOT NULL,
    prior_text      TEXT,
    new_text        TEXT,
    operational_areas TEXT[]      NOT NULL DEFAULT '{}',
    keywords        TEXT[]        NOT NULL DEFAULT '{}',
    change_index    INTEGER       NOT NULL DEFAULT 0
);

CREATE INDEX idx_reg_changes_diff
    ON regulatory.policy_changes (diff_id);
CREATE INDEX idx_reg_changes_severity
    ON regulatory.policy_changes (severity);

-- ── Drift reports ─────────────────────────────────────────────────────────────

CREATE TABLE regulatory.drift_reports (
    report_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID          NOT NULL REFERENCES saas.tenants (tenant_id) ON DELETE CASCADE,
    detected_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    overall_severity VARCHAR(20)  NOT NULL,
    finding_count   INTEGER       NOT NULL DEFAULT 0,
    docs_checked    INTEGER       NOT NULL DEFAULT 0,
    domains_checked TEXT[]        NOT NULL DEFAULT '{}',
    summary         TEXT          NOT NULL
);

CREATE INDEX idx_reg_drift_tenant
    ON regulatory.drift_reports (tenant_id, detected_at DESC);
CREATE INDEX idx_reg_drift_severity
    ON regulatory.drift_reports (overall_severity);

-- ── Drift findings ────────────────────────────────────────────────────────────

CREATE TABLE regulatory.drift_findings (
    finding_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID          NOT NULL REFERENCES regulatory.drift_reports (report_id) ON DELETE CASCADE,
    drift_type      VARCHAR(30)   NOT NULL,
    severity        VARCHAR(20)   NOT NULL,
    title           VARCHAR(300)  NOT NULL,
    description     TEXT          NOT NULL,
    affected_docs   TEXT[]        NOT NULL DEFAULT '{}',
    affected_rules  TEXT[]        NOT NULL DEFAULT '{}',
    affected_workflows TEXT[]     NOT NULL DEFAULT '{}',
    diff_id         VARCHAR(36)   REFERENCES regulatory.policy_diffs (diff_id),
    evidence        TEXT[]        NOT NULL DEFAULT '{}',
    recommendation  TEXT,
    detected_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_findings_report
    ON regulatory.drift_findings (report_id);
CREATE INDEX idx_reg_findings_severity
    ON regulatory.drift_findings (severity);
CREATE INDEX idx_reg_findings_type
    ON regulatory.drift_findings (drift_type);

-- ── Impact reports ────────────────────────────────────────────────────────────

CREATE TABLE regulatory.impact_reports (
    report_id       VARCHAR(36)   PRIMARY KEY,    -- UUID string
    tenant_id       UUID          NOT NULL REFERENCES saas.tenants (tenant_id) ON DELETE CASCADE,
    source_type     VARCHAR(20)   NOT NULL,        -- "diff" | "drift"
    source_id       VARCHAR(36)   NOT NULL,
    severity        VARCHAR(20)   NOT NULL,
    affected_domain_count INTEGER NOT NULL DEFAULT 0,
    workflow_count  INTEGER       NOT NULL DEFAULT 0,
    rule_count      INTEGER       NOT NULL DEFAULT 0,
    entity_count    INTEGER       NOT NULL DEFAULT 0,
    narrative       TEXT,
    action_required_by VARCHAR(20),               -- ISO-8601 date
    -- Financial risk estimate
    fin_risk_low_usd    BIGINT,
    fin_risk_high_usd   BIGINT,
    fin_risk_basis      TEXT,
    fin_risk_confidence FLOAT,
    analyzed_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_impact_tenant
    ON regulatory.impact_reports (tenant_id);
CREATE INDEX idx_reg_impact_source
    ON regulatory.impact_reports (source_type, source_id);

-- ── Affected elements ─────────────────────────────────────────────────────────

CREATE TABLE regulatory.affected_elements (
    element_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       VARCHAR(36)   NOT NULL REFERENCES regulatory.impact_reports (report_id) ON DELETE CASCADE,
    external_id     VARCHAR(120)  NOT NULL,        -- workflow_id, rule_code, entity_id, etc.
    element_type    VARCHAR(30)   NOT NULL,        -- ImpactDimension enum
    name            VARCHAR(300),
    severity        VARCHAR(20)   NOT NULL,
    remediation_required BOOLEAN  NOT NULL DEFAULT FALSE,
    notes           TEXT
);

CREATE INDEX idx_reg_affected_report
    ON regulatory.affected_elements (report_id);
CREATE INDEX idx_reg_affected_type
    ON regulatory.affected_elements (element_type);

-- ── Policy recommendations ────────────────────────────────────────────────────

CREATE TABLE regulatory.policy_recommendations (
    rec_id          VARCHAR(32)   PRIMARY KEY,    -- rec_<hex16>
    tenant_id       UUID          NOT NULL REFERENCES saas.tenants (tenant_id) ON DELETE CASCADE,
    rec_type        VARCHAR(40)   NOT NULL,
    title           VARCHAR(500)  NOT NULL,
    rationale       TEXT          NOT NULL,
    proposed_change TEXT          NOT NULL,
    affected_elements TEXT[]      NOT NULL DEFAULT '{}',
    source_type     VARCHAR(20)   NOT NULL,
    source_id       VARCHAR(36)   NOT NULL,
    status          VARCHAR(20)   NOT NULL DEFAULT 'draft',
    priority        VARCHAR(10)   NOT NULL DEFAULT 'normal',
    content_hash    CHAR(64)      NOT NULL,
    version         INTEGER       NOT NULL DEFAULT 1,
    prior_rec_id    VARCHAR(32)   REFERENCES regulatory.policy_recommendations (rec_id),
    created_by      VARCHAR(120)  NOT NULL DEFAULT 'system',
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    submitted_at    TIMESTAMPTZ,
    decided_at      TIMESTAMPTZ,
    decided_by      VARCHAR(120),
    decision_notes  TEXT,
    implemented_at  TIMESTAMPTZ,
    action_by_date  VARCHAR(20),                  -- ISO-8601 deadline
    metadata        JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reg_recs_tenant_status
    ON regulatory.policy_recommendations (tenant_id, status);
CREATE INDEX idx_reg_recs_priority
    ON regulatory.policy_recommendations (priority);
CREATE INDEX idx_reg_recs_source
    ON regulatory.policy_recommendations (source_type, source_id);
CREATE INDEX idx_reg_recs_created
    ON regulatory.policy_recommendations (created_at DESC);
CREATE INDEX idx_reg_recs_action_by
    ON regulatory.policy_recommendations (action_by_date) WHERE action_by_date IS NOT NULL;

-- ── Recommendation lineage ────────────────────────────────────────────────────

CREATE TABLE regulatory.recommendation_lineage (
    entry_id        UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    rec_id          VARCHAR(32)   NOT NULL REFERENCES regulatory.policy_recommendations (rec_id) ON DELETE CASCADE,
    event           VARCHAR(30)   NOT NULL,
    actor_id        VARCHAR(120)  NOT NULL,
    notes           TEXT,
    content_hash    CHAR(64)      NOT NULL,
    occurred_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_lineage_rec
    ON regulatory.recommendation_lineage (rec_id, occurred_at);

-- ── Policy citations ──────────────────────────────────────────────────────────

CREATE TABLE regulatory.policy_citations (
    citation_id     UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id VARCHAR(36)  NOT NULL,        -- external investigation ID
    doc_id          VARCHAR(32)   NOT NULL REFERENCES regulatory.regulatory_documents (doc_id),
    doc_version     VARCHAR(40)   NOT NULL,
    doc_title       VARCHAR(500)  NOT NULL,
    section         VARCHAR(200)  NOT NULL,
    excerpt         VARCHAR(500),
    rationale       TEXT,
    strength        VARCHAR(20)   NOT NULL,        -- CitationStrength enum
    domain          VARCHAR(80),
    asserted_by     VARCHAR(120)  NOT NULL DEFAULT 'system',
    asserted_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    effective_at    VARCHAR(20),                   -- ISO-8601 date
    confidence      FLOAT         NOT NULL DEFAULT 1.0,
    human_verified  BOOLEAN       NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_reg_citations_investigation
    ON regulatory.policy_citations (investigation_id);
CREATE INDEX idx_reg_citations_doc
    ON regulatory.policy_citations (doc_id);
CREATE INDEX idx_reg_citations_strength
    ON regulatory.policy_citations (strength);

-- ── Investigation policy contexts ─────────────────────────────────────────────

CREATE TABLE regulatory.investigation_policy_contexts (
    context_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id    VARCHAR(36)   NOT NULL,
    tenant_id           UUID          NOT NULL REFERENCES saas.tenants (tenant_id) ON DELETE CASCADE,
    context_as_of       TIMESTAMPTZ   NOT NULL,
    applicable_domains  TEXT[]        NOT NULL DEFAULT '{}',
    escalation_policy_notes TEXT,
    compliance_rationale TEXT,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by          VARCHAR(120)  NOT NULL DEFAULT 'system',
    metadata            JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reg_inv_contexts_investigation
    ON regulatory.investigation_policy_contexts (investigation_id);
CREATE INDEX idx_reg_inv_contexts_tenant
    ON regulatory.investigation_policy_contexts (tenant_id);

-- ── Readiness snapshots ───────────────────────────────────────────────────────

CREATE TABLE regulatory.readiness_snapshots (
    snapshot_id     UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID          NOT NULL REFERENCES saas.tenants (tenant_id) ON DELETE CASCADE,
    assessed_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    score           FLOAT         NOT NULL,
    band            VARCHAR(20)   NOT NULL,
    total_deduction FLOAT         NOT NULL DEFAULT 0.0,
    domains_covered TEXT[]        NOT NULL DEFAULT '{}',
    domains_missing TEXT[]        NOT NULL DEFAULT '{}',
    docs_evaluated  INTEGER       NOT NULL DEFAULT 0,
    drift_findings  INTEGER       NOT NULL DEFAULT 0,
    pending_recs    INTEGER       NOT NULL DEFAULT 0,
    summary         TEXT,
    generated_by    VARCHAR(120)  NOT NULL DEFAULT 'system'
);

CREATE INDEX idx_reg_readiness_tenant
    ON regulatory.readiness_snapshots (tenant_id, assessed_at DESC);
CREATE INDEX idx_reg_readiness_band
    ON regulatory.readiness_snapshots (band);

-- ── Readiness signals ─────────────────────────────────────────────────────────

CREATE TABLE regulatory.readiness_signals (
    signal_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id     UUID          NOT NULL REFERENCES regulatory.readiness_snapshots (snapshot_id) ON DELETE CASCADE,
    name            VARCHAR(120)  NOT NULL,
    category        VARCHAR(30)   NOT NULL,
    deduction       FLOAT         NOT NULL,
    reason          TEXT          NOT NULL,
    severity        VARCHAR(20)   NOT NULL,
    affected_ids    TEXT[]        NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reg_signals_snapshot
    ON regulatory.readiness_signals (snapshot_id);
CREATE INDEX idx_reg_signals_severity
    ON regulatory.readiness_signals (severity);

-- ── Timeline events ───────────────────────────────────────────────────────────

CREATE TABLE regulatory.timeline_events (
    event_id        UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID          NOT NULL REFERENCES saas.tenants (tenant_id) ON DELETE CASCADE,
    event_type      VARCHAR(40)   NOT NULL,
    occurred_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    title           VARCHAR(300)  NOT NULL,
    description     TEXT,
    external_id     VARCHAR(120)  NOT NULL,
    external_type   VARCHAR(40)   NOT NULL,
    severity        VARCHAR(20)   NOT NULL DEFAULT 'informational',
    actor_id        VARCHAR(120)  NOT NULL DEFAULT 'system',
    domain          VARCHAR(80),
    tags            TEXT[]        NOT NULL DEFAULT '{}',
    metadata        JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reg_timeline_tenant
    ON regulatory.timeline_events (tenant_id, occurred_at DESC);
CREATE INDEX idx_reg_timeline_external
    ON regulatory.timeline_events (external_id, external_type);
CREATE INDEX idx_reg_timeline_severity
    ON regulatory.timeline_events (severity) WHERE severity IN ('high','critical');
CREATE INDEX idx_reg_timeline_type
    ON regulatory.timeline_events (event_type);

-- ── Activation workflows ──────────────────────────────────────────────────────

CREATE TABLE regulatory.activation_workflows (
    workflow_id         UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID          NOT NULL REFERENCES saas.tenants (tenant_id) ON DELETE CASCADE,
    doc_id              VARCHAR(32)   NOT NULL REFERENCES regulatory.regulatory_documents (doc_id),
    doc_version         VARCHAR(40)   NOT NULL,
    doc_title           VARCHAR(500)  NOT NULL,
    status              VARCHAR(30)   NOT NULL DEFAULT 'pending_review',
    priority            VARCHAR(10)   NOT NULL DEFAULT 'normal',
    created_by          VARCHAR(120)  NOT NULL,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    reviewer_id         VARCHAR(120),
    review_started_at   TIMESTAMPTZ,
    approver_id         VARCHAR(120),
    approved_at         TIMESTAMPTZ,
    approval_notes      TEXT,
    activator_id        VARCHAR(120),
    activated_at        TIMESTAMPTZ,
    rejected_by         VARCHAR(120),
    rejected_at         TIMESTAMPTZ,
    rejection_reason    TEXT,
    action_required_by  VARCHAR(20),
    metadata            JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reg_workflows_tenant_status
    ON regulatory.activation_workflows (tenant_id, status);
CREATE INDEX idx_reg_workflows_doc
    ON regulatory.activation_workflows (doc_id);
CREATE INDEX idx_reg_workflows_pending
    ON regulatory.activation_workflows (tenant_id)
    WHERE status NOT IN ('activated','rejected','superseded','withdrawn');

-- ── Workflow audit entries ────────────────────────────────────────────────────

CREATE TABLE regulatory.workflow_audit_entries (
    entry_id        UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID          NOT NULL REFERENCES regulatory.activation_workflows (workflow_id) ON DELETE CASCADE,
    from_status     VARCHAR(30),
    to_status       VARCHAR(30)   NOT NULL,
    actor_id        VARCHAR(120)  NOT NULL,
    action          VARCHAR(60)   NOT NULL,
    notes           TEXT,
    occurred_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_wf_audit_workflow
    ON regulatory.workflow_audit_entries (workflow_id, occurred_at);

-- ── Regulatory graph nodes ────────────────────────────────────────────────────

CREATE TABLE regulatory.graph_nodes (
    node_id         VARCHAR(24)   PRIMARY KEY,    -- gn_<hex12>
    node_type       VARCHAR(30)   NOT NULL,
    label           VARCHAR(300)  NOT NULL,
    external_id     VARCHAR(120)  NOT NULL,
    domain          VARCHAR(80),
    valid_from      TIMESTAMPTZ,
    valid_until     TIMESTAMPTZ,
    properties      JSONB         NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_nodes_type
    ON regulatory.graph_nodes (node_type);
CREATE INDEX idx_reg_nodes_external
    ON regulatory.graph_nodes (external_id);
CREATE INDEX idx_reg_nodes_domain
    ON regulatory.graph_nodes (domain) WHERE domain IS NOT NULL;

-- ── Regulatory graph edges ────────────────────────────────────────────────────

CREATE TABLE regulatory.graph_edges (
    edge_id         VARCHAR(24)   PRIMARY KEY,    -- ge_<hex12>
    source_id       VARCHAR(24)   NOT NULL REFERENCES regulatory.graph_nodes (node_id) ON DELETE CASCADE,
    target_id       VARCHAR(24)   NOT NULL REFERENCES regulatory.graph_nodes (node_id) ON DELETE CASCADE,
    relationship    VARCHAR(30)   NOT NULL,
    confidence      FLOAT         NOT NULL DEFAULT 1.0,
    valid_from      TIMESTAMPTZ,
    valid_until     TIMESTAMPTZ,
    properties      JSONB         NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(120)  NOT NULL DEFAULT 'system'
);

CREATE INDEX idx_reg_edges_source
    ON regulatory.graph_edges (source_id);
CREATE INDEX idx_reg_edges_target
    ON regulatory.graph_edges (target_id);
CREATE INDEX idx_reg_edges_relationship
    ON regulatory.graph_edges (relationship);
CREATE INDEX idx_reg_edges_active
    ON regulatory.graph_edges (source_id, relationship)
    WHERE valid_until IS NULL;

-- ── Evaluation scenarios ──────────────────────────────────────────────────────

CREATE TABLE regulatory.evaluation_scenarios (
    scenario_id     UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(200)  NOT NULL,
    description     TEXT,
    tenant_id       UUID          NOT NULL,
    stages          TEXT[]        NOT NULL DEFAULT '{}',
    assertion_count INTEGER       NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    metadata        JSONB         NOT NULL DEFAULT '{}'
);

-- ── Evaluation results ────────────────────────────────────────────────────────

CREATE TABLE regulatory.evaluation_results (
    run_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id     UUID          NOT NULL REFERENCES regulatory.evaluation_scenarios (scenario_id) ON DELETE CASCADE,
    tenant_id       UUID          NOT NULL,
    run_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    passed          BOOLEAN       NOT NULL,
    pass_count      INTEGER       NOT NULL DEFAULT 0,
    fail_count      INTEGER       NOT NULL DEFAULT 0,
    stages_executed TEXT[]        NOT NULL DEFAULT '{}',
    duration_ms     FLOAT,
    error           TEXT,
    result_summary  JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reg_eval_results_scenario
    ON regulatory.evaluation_results (scenario_id, run_at DESC);
CREATE INDEX idx_reg_eval_results_passed
    ON regulatory.evaluation_results (passed, run_at DESC);

-- ── Seed: required policy domains ─────────────────────────────────────────────

INSERT INTO regulatory.policy_domains (domain, label, description, required) VALUES
    ('drug_340b',          '340B Drug Pricing Program',        'HRSA 340B program eligibility, compliance, and audit requirements.',                                 TRUE),
    ('contract_pharmacy',  'Contract Pharmacy',                 'Contract pharmacy arrangement regulations and oversight requirements.',                             TRUE),
    ('audit_requirements', 'Audit Requirements',                'Federal and state audit, record-keeping, and examination requirements.',                            TRUE),
    ('medicaid_exclusions','Medicaid Drug Rebate Exclusions',   'Medicaid duplicate discount prevention and state agency coordination requirements.',                FALSE),
    ('manufacturer_access','Manufacturer Access Restrictions',  'Drug manufacturer obligations and restrictions under 340B program guidance.',                       FALSE),
    ('covered_entity',     'Covered Entity Eligibility',        'HRSA covered entity eligibility, registration, and recertification requirements.',                  FALSE),
    ('hipaa_privacy',      'HIPAA Privacy & Security',          'Protected health information handling, breach notification, and security rule compliance.',          FALSE),
    ('cms_billing',        'CMS Billing & Claims',              'Centers for Medicare & Medicaid Services billing, claims submission, and reimbursement regulations.',FALSE),
    ('state_pharmacy_law', 'State Pharmacy Law',                'State board of pharmacy licensing, dispensing, and operational requirements.',                      FALSE),
    ('dea_controlled',     'DEA Controlled Substances',         'Drug Enforcement Administration controlled substance scheduling, dispensing, and record-keeping.',  FALSE)
ON CONFLICT (domain) DO NOTHING;
