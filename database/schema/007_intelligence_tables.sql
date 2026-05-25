-- ============================================================
-- Phase 7 — Enterprise Intelligence & Continuous Compliance
-- ============================================================

-- 1. Monitoring runs — tracks each scheduled/manual monitoring execution
CREATE TABLE audit.monitoring_runs (
    run_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type            VARCHAR(50)  NOT NULL,  -- scheduled_hourly|scheduled_daily|manual
    window_type         VARCHAR(20)  NOT NULL,  -- rolling_30d|rolling_60d|rolling_90d|custom
    window_start        TIMESTAMPTZ  NOT NULL,
    window_end          TIMESTAMPTZ  NOT NULL,
    status              VARCHAR(20)  NOT NULL DEFAULT 'running', -- running|completed|failed
    findings_evaluated  INTEGER      NOT NULL DEFAULT 0,
    new_findings        INTEGER      NOT NULL DEFAULT 0,
    drifts_detected     INTEGER      NOT NULL DEFAULT 0,
    correlations_found  INTEGER      NOT NULL DEFAULT 0,
    run_metadata        JSONB        NOT NULL DEFAULT '{}',
    error_message       TEXT,
    started_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX idx_monitoring_runs_status      ON audit.monitoring_runs (status);
CREATE INDEX idx_monitoring_runs_started_at  ON audit.monitoring_runs (started_at DESC);
CREATE INDEX idx_monitoring_runs_window_type ON audit.monitoring_runs (window_type, window_start);

-- 2. Compliance trends — rolling window metrics per entity × rule
CREATE TABLE audit.compliance_trends (
    trend_id         UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id        UUID    NOT NULL,
    entity_type      VARCHAR(50) NOT NULL,  -- covered_entity|pharmacy|provider|ndc
    rule_code        VARCHAR(20),           -- NULL = aggregate across all rules
    window_type      VARCHAR(20) NOT NULL,
    window_start     DATE    NOT NULL,
    window_end       DATE    NOT NULL,
    finding_count    INTEGER NOT NULL DEFAULT 0,
    critical_count   INTEGER NOT NULL DEFAULT 0,
    high_count       INTEGER NOT NULL DEFAULT 0,
    financial_exposure NUMERIC(15,2),
    risk_score       NUMERIC(5,4),
    trend_direction  VARCHAR(20),           -- increasing|decreasing|stable
    velocity         NUMERIC(10,4),         -- findings per day
    acceleration     NUMERIC(10,4),         -- change in velocity
    prior_period_count INTEGER,             -- for delta computation
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    monitoring_run_id UUID REFERENCES audit.monitoring_runs(run_id),
    UNIQUE (entity_id, entity_type, rule_code, window_type, window_start)
);

CREATE INDEX idx_compliance_trends_entity  ON audit.compliance_trends (entity_id, entity_type);
CREATE INDEX idx_compliance_trends_rule    ON audit.compliance_trends (rule_code, window_type);
CREATE INDEX idx_compliance_trends_dir     ON audit.compliance_trends (trend_direction, computed_at DESC);

-- 3. Entity risk scores — daily rolling risk scores per entity
CREATE TABLE audit.entity_risk_scores (
    score_id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id             UUID    NOT NULL,
    entity_type           VARCHAR(50) NOT NULL,
    score_date            DATE    NOT NULL,
    composite_score       NUMERIC(5,4) NOT NULL,
    finding_velocity      NUMERIC(10,4),      -- findings/day over trailing 30d
    exposure_trajectory   NUMERIC(15,2),      -- projected 30d exposure
    escalation_probability NUMERIC(5,4),
    trend_direction       VARCHAR(20),
    score_components      JSONB   NOT NULL DEFAULT '{}',
    computed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, entity_type, score_date)
);

CREATE INDEX idx_entity_risk_scores_entity ON audit.entity_risk_scores (entity_id, entity_type);
CREATE INDEX idx_entity_risk_scores_date   ON audit.entity_risk_scores (score_date DESC);
CREATE INDEX idx_entity_risk_scores_score  ON audit.entity_risk_scores (composite_score DESC);

-- 4. Cross-case correlations — intelligence links between investigation cases
CREATE TABLE audit.cross_case_correlations (
    correlation_id   UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id_a        UUID    NOT NULL REFERENCES audit.investigation_cases(case_id),
    case_id_b        UUID    NOT NULL REFERENCES audit.investigation_cases(case_id),
    correlation_type VARCHAR(50) NOT NULL, -- shared_pharmacy|shared_ndc|shared_provider|pattern_match|temporal_cluster
    strength         NUMERIC(5,4) NOT NULL CHECK (strength BETWEEN 0 AND 1),
    shared_entities  JSONB   NOT NULL DEFAULT '{}',
    explanation      TEXT,
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    monitoring_run_id UUID REFERENCES audit.monitoring_runs(run_id),
    UNIQUE (case_id_a, case_id_b, correlation_type)
);

CREATE INDEX idx_cross_case_corr_a        ON audit.cross_case_correlations (case_id_a);
CREATE INDEX idx_cross_case_corr_b        ON audit.cross_case_correlations (case_id_b);
CREATE INDEX idx_cross_case_corr_type     ON audit.cross_case_correlations (correlation_type, strength DESC);
CREATE INDEX idx_cross_case_corr_strength ON audit.cross_case_correlations (strength DESC);

-- 5. Intelligence graph edges — adjacency table for compliance knowledge graph
CREATE TABLE audit.intelligence_graph_edges (
    edge_id           UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type       VARCHAR(50) NOT NULL, -- covered_entity|pharmacy|provider|ndc|finding|case
    source_id         VARCHAR(255) NOT NULL, -- UUID or string identifier
    target_type       VARCHAR(50) NOT NULL,
    target_id         VARCHAR(255) NOT NULL,
    relationship      VARCHAR(50) NOT NULL,  -- involves|dispensed_at|prescribed_by|contains_drug|grouped_in|correlated_with
    weight            NUMERIC(8,4) NOT NULL DEFAULT 1.0,
    properties        JSONB   NOT NULL DEFAULT '{}',
    valid_from        DATE    NOT NULL,
    valid_to          DATE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, source_id, target_type, target_id, relationship)
);

CREATE INDEX idx_graph_edges_source   ON audit.intelligence_graph_edges (source_type, source_id);
CREATE INDEX idx_graph_edges_target   ON audit.intelligence_graph_edges (target_type, target_id);
CREATE INDEX idx_graph_edges_rel      ON audit.intelligence_graph_edges (relationship);
CREATE INDEX idx_graph_edges_weight   ON audit.intelligence_graph_edges (weight DESC);

-- 6. Copilot sessions — investigator assistance session records
--    Read-only assistance — copilot cannot modify case data
CREATE TABLE audit.copilot_sessions (
    session_id       UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id          UUID    NOT NULL REFERENCES audit.investigation_cases(case_id),
    investigator_id  VARCHAR(255),
    session_type     VARCHAR(50) NOT NULL, -- summarize|timeline|recommend|navigate|related_cases
    input_context    JSONB   NOT NULL DEFAULT '{}',
    output           JSONB   NOT NULL DEFAULT '{}',
    model_id         VARCHAR(100),
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_tokens     INTEGER NOT NULL DEFAULT 0,
    latency_ms       NUMERIC(10,2),
    confidence_score NUMERIC(5,4),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_copilot_sessions_case ON audit.copilot_sessions (case_id, created_at DESC);
CREATE INDEX idx_copilot_sessions_type ON audit.copilot_sessions (session_type);

-- 7. Analyst overrides — false positives, risk level changes, calibration data
CREATE TABLE audit.analyst_overrides (
    override_id    UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id     UUID    REFERENCES audit.audit_findings(finding_id),
    case_id        UUID    REFERENCES audit.investigation_cases(case_id),
    analyst_id     VARCHAR(255) NOT NULL,
    override_type  VARCHAR(50) NOT NULL, -- false_positive|risk_level|escalation|status
    original_value JSONB   NOT NULL DEFAULT '{}',
    override_value JSONB   NOT NULL DEFAULT '{}',
    rationale      TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_analyst_overrides_finding ON audit.analyst_overrides (finding_id);
CREATE INDEX idx_analyst_overrides_case    ON audit.analyst_overrides (case_id);
CREATE INDEX idx_analyst_overrides_type    ON audit.analyst_overrides (override_type, created_at DESC);

-- 8. Evaluation runs — advanced evaluation framework persistence
CREATE TABLE audit.evaluation_runs (
    eval_id        UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_type      VARCHAR(50) NOT NULL, -- golden_replay|drift_check|calibration|regression|longitudinal
    eval_name      VARCHAR(255),
    status         VARCHAR(20) NOT NULL DEFAULT 'running',
    passed         BOOLEAN,
    total_checks   INTEGER NOT NULL DEFAULT 0,
    failed_checks  INTEGER NOT NULL DEFAULT 0,
    eval_metadata  JSONB   NOT NULL DEFAULT '{}',
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ
);

CREATE INDEX idx_eval_runs_type   ON audit.evaluation_runs (eval_type, started_at DESC);
CREATE INDEX idx_eval_runs_status ON audit.evaluation_runs (status);

COMMENT ON TABLE audit.monitoring_runs          IS 'Phase 7: Scheduled/manual monitoring execution records';
COMMENT ON TABLE audit.compliance_trends        IS 'Phase 7: Rolling window compliance trend data per entity';
COMMENT ON TABLE audit.entity_risk_scores       IS 'Phase 7: Daily rolling risk scores per covered entity/pharmacy';
COMMENT ON TABLE audit.cross_case_correlations  IS 'Phase 7: Intelligence correlations between investigation cases';
COMMENT ON TABLE audit.intelligence_graph_edges IS 'Phase 7: Compliance knowledge graph adjacency table';
COMMENT ON TABLE audit.copilot_sessions         IS 'Phase 7: Investigator copilot assistance sessions (read-only)';
COMMENT ON TABLE audit.analyst_overrides        IS 'Phase 7: Analyst override records for calibration and false-positive tracking';
COMMENT ON TABLE audit.evaluation_runs          IS 'Phase 7: Advanced evaluation framework run persistence';
