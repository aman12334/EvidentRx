-- ============================================================================
-- Phase 11: Continuous Learning, Human Feedback & Adaptive Intelligence Layer
-- Schema: learning
-- ============================================================================
-- Tables created:
--   learning.feedback_records          — immutable analyst feedback events
--   learning.feedback_lineage          — cryptographic lineage chain per feedback
--   learning.calibration_snapshots     — versioned risk calibration snapshots
--   learning.prompt_versions           — versioned prompt templates
--   learning.workflow_versions         — versioned workflow definitions
--   learning.benchmark_suites          — versioned evaluation benchmark suites
--   learning.evaluation_runs           — evaluation harness run records
--   learning.experiments               — A/B experiment definitions
--   learning.experiment_runs           — individual experiment run records
--   learning.approval_requests         — approval gate requests
--   learning.approval_decisions        — individual approval/rejection decisions
--   learning.memory_entries            — adaptive intelligence memory store
--   learning.governance_audit          — immutable governance audit chain
--   learning.recommendation_records    — recommendation lifecycle tracking
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS learning;

-- ── Touch-updated-at trigger (shared) ─────────────────────────────────────────

CREATE OR REPLACE FUNCTION learning.touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ── 1. Feedback records ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.feedback_records (
    feedback_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT          NOT NULL,
    analyst_id        TEXT          NOT NULL,
    feedback_type     TEXT          NOT NULL,         -- FeedbackType enum value
    artifact_type     TEXT          NOT NULL,
    artifact_id       TEXT          NOT NULL,
    status            TEXT          NOT NULL DEFAULT 'pending',
    lineage_hash      TEXT          NOT NULL,         -- SHA-256 of semantic content
    content           JSONB         NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    -- Partitioned tenant isolation
    CONSTRAINT feedback_type_check CHECK (
        feedback_type IN (
            'false_positive_report','false_negative_escalation','outcome_label',
            'remediation_outcome','confidence_override','recommendation_rating',
            'investigation_quality','workflow_feedback'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_feedback_tenant_type
    ON learning.feedback_records (tenant_id, feedback_type);
CREATE INDEX IF NOT EXISTS idx_feedback_artifact
    ON learning.feedback_records (tenant_id, artifact_id);
CREATE INDEX IF NOT EXISTS idx_feedback_analyst
    ON learning.feedback_records (tenant_id, analyst_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at
    ON learning.feedback_records (created_at DESC);


-- ── 2. Feedback lineage ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.feedback_lineage (
    entry_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    feedback_id    UUID          NOT NULL REFERENCES learning.feedback_records(feedback_id),
    tenant_id      TEXT          NOT NULL,
    event_type     TEXT          NOT NULL,
    prior_hash     TEXT,                              -- NULL for genesis entry
    chain_hash     TEXT          NOT NULL,            -- SHA-256(prior_hash + content_hash)
    content_hash   TEXT          NOT NULL,
    actor          TEXT          NOT NULL,
    occurred_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    metadata       JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_feedback_lineage_feedback
    ON learning.feedback_lineage (feedback_id);
CREATE INDEX IF NOT EXISTS idx_feedback_lineage_tenant
    ON learning.feedback_lineage (tenant_id, occurred_at DESC);


-- ── 3. Calibration snapshots ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.calibration_snapshots (
    snapshot_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT          NOT NULL,
    version           TEXT          NOT NULL,
    status            TEXT          NOT NULL DEFAULT 'draft',
    rule_calibrations JSONB         NOT NULL DEFAULT '{}',
    thresholds        JSONB         NOT NULL DEFAULT '{}',
    feedback_window_days INT        NOT NULL DEFAULT 90,
    total_fp          INT           NOT NULL DEFAULT 0,
    total_fn          INT           NOT NULL DEFAULT 0,
    total_confirmed   INT           NOT NULL DEFAULT 0,
    total_cleared     INT           NOT NULL DEFAULT 0,
    content_hash      TEXT          NOT NULL,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by        TEXT          NOT NULL,
    approved_by       TEXT,
    approved_at       TIMESTAMPTZ,
    activated_at      TIMESTAMPTZ,
    superseded_at     TIMESTAMPTZ,
    CONSTRAINT calibration_status_check CHECK (
        status IN ('draft','pending_approval','approved','active','rejected','superseded')
    )
);

CREATE INDEX IF NOT EXISTS idx_calibration_tenant_status
    ON learning.calibration_snapshots (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_calibration_active
    ON learning.calibration_snapshots (tenant_id)
    WHERE status = 'active';


-- ── 4. Prompt versions ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.prompt_versions (
    prompt_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          TEXT          NOT NULL,
    prompt_name        TEXT          NOT NULL,
    version            TEXT          NOT NULL,
    title              TEXT          NOT NULL,
    template           TEXT          NOT NULL,
    system_context     TEXT          NOT NULL DEFAULT '',
    model_target       TEXT          NOT NULL,
    status             TEXT          NOT NULL DEFAULT 'draft',
    content_hash       TEXT          NOT NULL,
    change_summary     TEXT          NOT NULL DEFAULT '',
    test_coverage      NUMERIC(4,3)  NOT NULL DEFAULT 0.0,
    parent_version_id  UUID          REFERENCES learning.prompt_versions(prompt_id),
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by         TEXT          NOT NULL,
    approved_by        TEXT,
    approved_at        TIMESTAMPTZ,
    metadata           JSONB         NOT NULL DEFAULT '{}',
    CONSTRAINT prompt_status_check CHECK (
        status IN ('draft','review','active','deprecated','rejected')
    ),
    UNIQUE (tenant_id, prompt_name, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_tenant_name
    ON learning.prompt_versions (tenant_id, prompt_name, status);
CREATE INDEX IF NOT EXISTS idx_prompt_active
    ON learning.prompt_versions (tenant_id, prompt_name)
    WHERE status = 'active';


-- ── 5. Workflow versions ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.workflow_versions (
    workflow_id        UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          TEXT          NOT NULL,
    workflow_name      TEXT          NOT NULL,
    version            TEXT          NOT NULL,
    title              TEXT          NOT NULL,
    description        TEXT          NOT NULL DEFAULT '',
    steps              JSONB         NOT NULL DEFAULT '[]',
    output_contract    JSONB         NOT NULL DEFAULT '{}',
    status             TEXT          NOT NULL DEFAULT 'draft',
    content_hash       TEXT          NOT NULL,
    change_summary     TEXT          NOT NULL DEFAULT '',
    min_agent_version  TEXT          NOT NULL DEFAULT '1.0.0',
    parent_version_id  UUID          REFERENCES learning.workflow_versions(workflow_id),
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by         TEXT          NOT NULL,
    approved_by        TEXT,
    approved_at        TIMESTAMPTZ,
    metadata           JSONB         NOT NULL DEFAULT '{}',
    CONSTRAINT workflow_status_check CHECK (
        status IN ('draft','review','active','deprecated','rejected')
    ),
    UNIQUE (tenant_id, workflow_name, version)
);

CREATE INDEX IF NOT EXISTS idx_workflow_tenant_name
    ON learning.workflow_versions (tenant_id, workflow_name, status);
CREATE INDEX IF NOT EXISTS idx_workflow_active
    ON learning.workflow_versions (tenant_id, workflow_name)
    WHERE status = 'active';


-- ── 6. Benchmark suites ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.benchmark_suites (
    benchmark_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT          NOT NULL,
    name              TEXT          NOT NULL,
    version           TEXT          NOT NULL,
    description       TEXT          NOT NULL DEFAULT '',
    status            TEXT          NOT NULL DEFAULT 'draft',
    case_count        INT           NOT NULL DEFAULT 0,
    content_hash      TEXT          NOT NULL DEFAULT '',
    category_distribution JSONB     NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by        TEXT          NOT NULL,
    published_at      TIMESTAMPTZ,
    parent_version    TEXT,
    CONSTRAINT benchmark_status_check CHECK (
        status IN ('draft','published','deprecated','archived')
    ),
    UNIQUE (tenant_id, name, version)
);

CREATE INDEX IF NOT EXISTS idx_benchmark_tenant
    ON learning.benchmark_suites (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_benchmark_published
    ON learning.benchmark_suites (tenant_id, published_at DESC)
    WHERE status = 'published';


-- ── 7. Evaluation runs ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.evaluation_runs (
    run_id               UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            TEXT          NOT NULL,
    benchmark_id         UUID          REFERENCES learning.benchmark_suites(benchmark_id),
    evaluation_type      TEXT          NOT NULL,
    prompt_version       TEXT,
    model_config         TEXT,
    calibration_version  TEXT,
    status               TEXT          NOT NULL DEFAULT 'pending',
    started_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    finished_at          TIMESTAMPTZ,
    case_count           INT           NOT NULL DEFAULT 0,
    avg_reasoning_score  NUMERIC(6,4),
    outcome_accuracy     NUMERIC(6,4),
    hallucination_rate   NUMERIC(6,4),
    avg_latency_seconds  NUMERIC(8,3),
    aggregate            JSONB         NOT NULL DEFAULT '{}',
    content_hash         TEXT          NOT NULL DEFAULT '',
    triggered_by         TEXT          NOT NULL DEFAULT 'system',
    run_config           JSONB         NOT NULL DEFAULT '{}',
    CONSTRAINT eval_type_check CHECK (
        evaluation_type IN (
            'replay','regression','longitudinal','cross_model','prompt','workflow'
        )
    ),
    CONSTRAINT eval_status_check CHECK (
        status IN ('pending','running','completed','failed','cancelled')
    )
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_tenant
    ON learning.evaluation_runs (tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_runs_benchmark
    ON learning.evaluation_runs (benchmark_id);


-- ── 8. Experiments ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.experiments (
    experiment_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          TEXT          NOT NULL,
    slot               TEXT          NOT NULL,
    name               TEXT          NOT NULL,
    experiment_type    TEXT          NOT NULL,
    description        TEXT          NOT NULL DEFAULT '',
    hypothesis         TEXT          NOT NULL DEFAULT '',
    benchmark_id       UUID          REFERENCES learning.benchmark_suites(benchmark_id),
    success_criteria   JSONB         NOT NULL DEFAULT '{}',
    control_config     JSONB         NOT NULL DEFAULT '{}',
    treatment_config   JSONB         NOT NULL DEFAULT '{}',
    state              TEXT          NOT NULL DEFAULT 'pending',
    traffic_fraction   NUMERIC(5,4)  NOT NULL DEFAULT 0.10,
    success_metric     TEXT          NOT NULL DEFAULT 'outcome_accuracy',
    min_detectable_effect NUMERIC(6,4) NOT NULL DEFAULT 0.02,
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by         TEXT          NOT NULL,
    approved_by        TEXT,
    start_at           TIMESTAMPTZ,
    stop_at            TIMESTAMPTZ   NOT NULL,
    concluded_at       TIMESTAMPTZ,
    conclusion         TEXT          NOT NULL DEFAULT '',
    metadata           JSONB         NOT NULL DEFAULT '{}',
    CONSTRAINT experiment_state_check CHECK (
        state IN ('pending','running','paused','completed','cancelled','failed')
    )
);

CREATE INDEX IF NOT EXISTS idx_experiments_tenant
    ON learning.experiments (tenant_id, state);
CREATE INDEX IF NOT EXISTS idx_experiments_active
    ON learning.experiments (tenant_id, slot)
    WHERE state = 'running';


-- ── 9. Experiment runs ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.experiment_runs (
    run_id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id      UUID          NOT NULL REFERENCES learning.experiments(experiment_id),
    tenant_id          TEXT          NOT NULL,
    snapshot           JSONB         NOT NULL DEFAULT '{}',  -- VersionSnapshot
    benchmark_id       UUID          REFERENCES learning.benchmark_suites(benchmark_id),
    evaluation_run_id  UUID          REFERENCES learning.evaluation_runs(run_id),
    status             TEXT          NOT NULL DEFAULT 'running',
    started_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    summary_metrics    JSONB         NOT NULL DEFAULT '{}',
    notes              TEXT          NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_exp_runs_experiment
    ON learning.experiment_runs (experiment_id);


-- ── 10. Approval requests ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.approval_requests (
    request_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT          NOT NULL,
    change_type     TEXT          NOT NULL,
    title           TEXT          NOT NULL,
    description     TEXT          NOT NULL DEFAULT '',
    requested_by    TEXT          NOT NULL,
    artifact_id     TEXT          NOT NULL,
    artifact_type   TEXT,
    change_payload  JSONB         NOT NULL DEFAULT '{}',
    status          TEXT          NOT NULL DEFAULT 'pending',
    min_approvers   INT           NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ   NOT NULL,
    CONSTRAINT approval_status_check CHECK (
        status IN ('pending','approved','rejected','expired','cancelled')
    )
);

CREATE INDEX IF NOT EXISTS idx_approvals_tenant_status
    ON learning.approval_requests (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_approvals_artifact
    ON learning.approval_requests (artifact_id);
CREATE INDEX IF NOT EXISTS idx_approvals_pending
    ON learning.approval_requests (tenant_id, expires_at)
    WHERE status = 'pending';


-- ── 11. Approval decisions ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.approval_decisions (
    decision_id    UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id     UUID          NOT NULL REFERENCES learning.approval_requests(request_id),
    reviewer       TEXT          NOT NULL,
    decision       TEXT          NOT NULL,       -- 'approved' | 'rejected'
    rationale      TEXT          NOT NULL DEFAULT '',
    decided_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    content_hash   TEXT          NOT NULL,
    CONSTRAINT decision_check CHECK (decision IN ('approved','rejected'))
);

CREATE INDEX IF NOT EXISTS idx_decisions_request
    ON learning.approval_decisions (request_id);


-- ── 12. Memory entries ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.memory_entries (
    entry_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      TEXT          NOT NULL,
    memory_type    TEXT          NOT NULL,
    content        JSONB         NOT NULL DEFAULT '{}',
    content_hash   TEXT          NOT NULL,
    recorded_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    recorded_by    TEXT          NOT NULL,
    expires_at     TIMESTAMPTZ   NOT NULL,
    tags           TEXT[]        NOT NULL DEFAULT '{}',
    supersedes_id  UUID          REFERENCES learning.memory_entries(entry_id),
    artifact_id    TEXT,
    CONSTRAINT memory_type_check CHECK (
        memory_type IN (
            'analyst_correction','investigation_outcome','calibration_event',
            'false_positive_signal','false_negative_signal',
            'recommendation_outcome','workflow_improvement','prompt_revision'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_memory_tenant_type
    ON learning.memory_entries (tenant_id, memory_type, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_artifact
    ON learning.memory_entries (tenant_id, artifact_id)
    WHERE artifact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_expires
    ON learning.memory_entries (expires_at)
    WHERE expires_at > NOW();
CREATE INDEX IF NOT EXISTS idx_memory_tags
    ON learning.memory_entries USING GIN (tags);


-- ── 13. Governance audit ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.governance_audit (
    audit_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      TEXT          NOT NULL,
    event_type     TEXT          NOT NULL,
    actor          TEXT          NOT NULL,
    artifact_id    TEXT,
    artifact_type  TEXT,
    payload        JSONB         NOT NULL DEFAULT '{}',
    occurred_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    content_hash   TEXT          NOT NULL,
    prior_hash     TEXT,
    chain_hash     TEXT          NOT NULL,
    source_ip      TEXT,
    session_id     TEXT
);

CREATE INDEX IF NOT EXISTS idx_gov_audit_tenant
    ON learning.governance_audit (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_gov_audit_actor
    ON learning.governance_audit (tenant_id, actor);
CREATE INDEX IF NOT EXISTS idx_gov_audit_artifact
    ON learning.governance_audit (tenant_id, artifact_id)
    WHERE artifact_id IS NOT NULL;


-- ── 14. Recommendation records ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learning.recommendation_records (
    rec_id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT          NOT NULL,
    case_id             TEXT          NOT NULL,
    recommendation_type TEXT          NOT NULL,
    version             TEXT          NOT NULL,
    content             JSONB         NOT NULL DEFAULT '{}',
    events              JSONB         NOT NULL DEFAULT '[]',   -- list of RecommendationEvent
    generated_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    generated_by        TEXT          NOT NULL DEFAULT 'system',
    outcome             TEXT,         -- 'effective' | 'ineffective' | NULL
    was_followed        BOOLEAN       NOT NULL DEFAULT FALSE,
    time_to_decision_hours NUMERIC(8,2)
);

CREATE INDEX IF NOT EXISTS idx_recs_tenant_case
    ON learning.recommendation_records (tenant_id, case_id);
CREATE INDEX IF NOT EXISTS idx_recs_tenant_type_version
    ON learning.recommendation_records (tenant_id, recommendation_type, version);
CREATE INDEX IF NOT EXISTS idx_recs_generated_at
    ON learning.recommendation_records (generated_at DESC);


-- ── Retention cleanup function ────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION learning.purge_expired_memory(p_tenant_id TEXT)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM learning.memory_entries
    WHERE tenant_id = p_tenant_id
      AND expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON SCHEMA learning IS
    'Continuous learning, human feedback, and adaptive intelligence layer. '
    'All tables are append-only within their retention windows.';
