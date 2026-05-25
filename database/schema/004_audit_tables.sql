-- =============================================================================
-- Script 004: Audit Intelligence Tables (audit schema)
--
-- Creation order (dependency-safe):
--   audit.compliance_rules        (no upstream audit deps)
--   audit.investigation_cases     (refs ref.covered_entities)
--   audit.audit_findings          (refs rules + cases + ops tables)
--   audit.reasoning_traces        (refs cases + findings)
-- =============================================================================

-- =============================================================================
-- audit.compliance_rules
-- Purpose     : Versioned registry of all 340B compliance rules.
--               The rules engine reads this table to drive deterministic checks.
-- Versioning  : rule_version (semver string) + parent_rule_id for lineage.
--               A new row is inserted when a rule changes — old rows are kept
--               for audit trail replay on historical findings.
-- Notes       : logic_definition JSONB stores rule parameters consumed by the
--               rules engine (thresholds, date windows, payer filters, etc.).
--               LLMs never modify this table — it is the deterministic source of truth.
-- =============================================================================
CREATE TABLE audit.compliance_rules (
    rule_id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity
    rule_code               VARCHAR(50) NOT NULL UNIQUE,    -- e.g. DD-001, MEO-003
    rule_name               VARCHAR(255) NOT NULL,
    rule_category           VARCHAR(50) NOT NULL,
    rule_version            VARCHAR(20) NOT NULL DEFAULT '1.0.0',
    parent_rule_id          UUID        REFERENCES audit.compliance_rules(rule_id),

    -- Definition
    description             TEXT,
    severity                VARCHAR(20) NOT NULL,
    logic_definition        JSONB       NOT NULL DEFAULT '{}',  -- engine-readable parameters
    regulatory_reference    TEXT,                               -- statutory / guidance citation

    -- Lifecycle
    effective_date          DATE        NOT NULL,
    expiration_date         DATE,
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_rule_severity CHECK (
        severity IN ('critical', 'high', 'medium', 'low', 'informational')
    ),
    CONSTRAINT ck_rule_category CHECK (rule_category IN (
        'duplicate_discount',
        'medicaid_overlap',
        'contract_pharmacy_eligibility',
        'split_billing',
        'carve_in_out',
        'entity_eligibility',
        'data_quality'
    ))
);

COMMENT ON TABLE  audit.compliance_rules                    IS 'Versioned 340B compliance rules — deterministic source of truth for the rules engine';
COMMENT ON COLUMN audit.compliance_rules.rule_code          IS 'Short code used in finding_code prefixes, e.g. DD-001 = duplicate discount rule 1';
COMMENT ON COLUMN audit.compliance_rules.rule_version       IS 'Semver string — bump on any change to logic_definition or severity';
COMMENT ON COLUMN audit.compliance_rules.parent_rule_id     IS 'Points to the prior version; enables rule lineage traversal for historical replay';
COMMENT ON COLUMN audit.compliance_rules.logic_definition   IS 'Structured parameters consumed by the rules engine (never by LLMs)';
COMMENT ON COLUMN audit.compliance_rules.regulatory_reference IS '340B statute / HRSA guidance citation supporting this rule';


-- =============================================================================
-- audit.investigation_cases
-- Purpose     : Workflow state for audit investigations.
--               Supports human-in-the-loop review and LangGraph agent orchestration.
-- Notes       : workflow_state JSONB stores serialized LangGraph graph state,
--               enabling pause / resume across agent invocations.
--               finding_count is a denormalized counter updated by the application
--               when findings are attached / detached.
-- =============================================================================
CREATE TABLE audit.investigation_cases (
    case_id                         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity
    case_number                     VARCHAR(50) NOT NULL UNIQUE,    -- e.g. INV-2025-00001
    covered_entity_id               UUID        NOT NULL REFERENCES ref.covered_entities(ce_id),
    case_type                       VARCHAR(50) NOT NULL,

    -- Workflow state
    status                          VARCHAR(30) NOT NULL DEFAULT 'open',
    priority                        VARCHAR(20) NOT NULL DEFAULT 'medium',
    title                           VARCHAR(500) NOT NULL,
    description                     TEXT,
    assigned_to                     VARCHAR(255),
    escalated_to                    VARCHAR(255),

    -- Dates
    opened_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_date                        DATE,
    closed_at                       TIMESTAMPTZ,

    -- Financial exposure
    financial_exposure_estimate     NUMERIC(15,2),   -- set by rules engine / AI estimate
    financial_exposure_confirmed    NUMERIC(15,2),   -- set after human review

    -- Aggregates (denormalized for dashboard queries)
    finding_count                   INTEGER     NOT NULL DEFAULT 0,

    -- LangGraph / agent orchestration
    agent_workflow_id               VARCHAR(255),   -- external workflow engine ID
    workflow_state                  JSONB       NOT NULL DEFAULT '{}',  -- serialized graph state
    workflow_checkpoint             TEXT,           -- LangGraph checkpoint identifier
    last_agent_activity_at          TIMESTAMPTZ,

    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_case_status CHECK (status IN (
        'open', 'in_progress', 'pending_review', 'escalated',
        'closed', 'dismissed', 'on_hold'
    )),
    CONSTRAINT ck_case_priority CHECK (priority IN ('critical', 'high', 'medium', 'low')),
    CONSTRAINT ck_case_type CHECK (case_type IN (
        'routine_audit', 'targeted_investigation', 'self_disclosure',
        'regulatory_inquiry', 'data_quality'
    ))
);

COMMENT ON TABLE  audit.investigation_cases                          IS 'Investigation workflow state — supports human review and LangGraph agent orchestration';
COMMENT ON COLUMN audit.investigation_cases.case_number             IS 'Human-readable case ID, e.g. INV-2025-00001';
COMMENT ON COLUMN audit.investigation_cases.workflow_state          IS 'Serialized LangGraph graph state — enables agent pause and resume';
COMMENT ON COLUMN audit.investigation_cases.agent_workflow_id       IS 'External workflow engine run ID (LangGraph, Temporal, Prefect, etc.)';
COMMENT ON COLUMN audit.investigation_cases.finding_count           IS 'Denormalized count — maintained by application layer on finding attach / detach';


-- =============================================================================
-- audit.audit_findings
-- Purpose     : Individual compliance violations detected by the rules engine.
--               Each finding is linked to the specific rule version that produced it,
--               enabling accurate historical replay if a rule is later revised.
-- Notes       : evidence_payload captures a full denormalized snapshot of the
--               evidence at detection time — ensuring the finding is self-contained
--               even if source records are later corrected or deleted.
--               Financial exposure is estimated by the rules engine; the AI layer
--               may refine the estimate and log reasoning in reasoning_traces.
-- =============================================================================
CREATE TABLE audit.audit_findings (
    finding_id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity
    finding_code                    VARCHAR(50) NOT NULL,           -- e.g. DD-2025-001234
    rule_id                         UUID        NOT NULL REFERENCES audit.compliance_rules(rule_id),
    rule_code                       VARCHAR(50) NOT NULL,           -- denormalized for fast filtering
    rule_version                    VARCHAR(20) NOT NULL,           -- denormalized for replay accuracy

    -- Entity
    covered_entity_id               UUID        NOT NULL REFERENCES ref.covered_entities(ce_id),
    investigation_case_id           UUID        REFERENCES audit.investigation_cases(case_id),

    -- Classification
    finding_type                    VARCHAR(50) NOT NULL,           -- mirrors rule_category
    severity                        VARCHAR(20) NOT NULL,
    status                          VARCHAR(30) NOT NULL DEFAULT 'open',

    -- Detection metadata
    detected_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detection_method                VARCHAR(30) NOT NULL DEFAULT 'rules_engine',
    confidence_score                NUMERIC(5,4),   -- 1.0 = deterministic; <1.0 = probabilistic

    -- Financial exposure
    financial_exposure              NUMERIC(15,2),
    financial_exposure_methodology  TEXT,

    -- Logical links to partitioned operational tables
    -- (not enforced as DB FKs — see script 003 notes)
    purchase_id                     UUID,
    purchase_date                   DATE,
    dispense_id                     UUID,
    dispense_date                   DATE,
    claim_id                        UUID,
    claim_service_date              DATE,
    split_billing_id                UUID        REFERENCES ops.split_billing(split_billing_id),

    -- Evidence snapshot (immutable at detection time)
    evidence_payload                JSONB       NOT NULL DEFAULT '{}',
    entity_references               JSONB       NOT NULL DEFAULT '{}',

    -- Violation period
    violation_period_start          DATE,
    violation_period_end            DATE,

    -- Resolution
    resolved_at                     TIMESTAMPTZ,
    resolved_by                     VARCHAR(255),
    resolution_type                 VARCHAR(30),
    resolution_notes                TEXT,

    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_finding_severity CHECK (
        severity IN ('critical', 'high', 'medium', 'low', 'informational')
    ),
    CONSTRAINT ck_finding_status CHECK (status IN (
        'open', 'under_review', 'confirmed', 'dismissed',
        'remediated', 'appealed', 'escalated'
    )),
    CONSTRAINT ck_finding_detection_method CHECK (detection_method IN (
        'rules_engine', 'manual', 'ai_flagged', 'imported'
    )),
    CONSTRAINT ck_finding_resolution_type CHECK (
        resolution_type IS NULL OR resolution_type IN (
            'confirmed_violation', 'false_positive', 'remediated',
            'appealed', 'insufficient_evidence'
        )
    ),
    CONSTRAINT ck_confidence_score CHECK (
        confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)
    )
);

COMMENT ON TABLE  audit.audit_findings                          IS 'Compliance violations — produced by the deterministic rules engine, not by LLMs';
COMMENT ON COLUMN audit.audit_findings.finding_code            IS 'Human-readable code, e.g. DD-2025-001234 (rule_prefix + year + sequence)';
COMMENT ON COLUMN audit.audit_findings.rule_version            IS 'Denormalized rule version — ensures historical replay uses the correct rule logic';
COMMENT ON COLUMN audit.audit_findings.confidence_score        IS '1.0 = fully deterministic violation; <1.0 = probabilistic (e.g. from pattern matching)';
COMMENT ON COLUMN audit.audit_findings.evidence_payload        IS 'Immutable snapshot of all evidence fields at detection — self-contained for audit replay';
COMMENT ON COLUMN audit.audit_findings.purchase_id             IS 'Logical FK to ops.purchases — not DB-enforced due to partitioned parent table';


-- =============================================================================
-- audit.reasoning_traces
-- Purpose     : Append-only log of every LLM reasoning step.
--               Provides full auditability of AI-assisted investigation decisions.
-- Design      : Intentionally append-only — no updated_at column.
--               parent_trace_id supports hierarchical reasoning chains in
--               multi-agent workflows.
-- LangGraph   : workflow_node maps to a LangGraph node name; workflow_step_sequence
--               preserves execution order within a graph run.
-- Cost control: cache_read_tokens / cache_write_tokens track prompt cache usage
--               for cost attribution per investigation.
-- =============================================================================
CREATE TABLE audit.reasoning_traces (
    trace_id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Session grouping
    session_id                  UUID        NOT NULL,       -- all traces in one agent session
    investigation_case_id       UUID        REFERENCES audit.investigation_cases(case_id),
    finding_id                  UUID        REFERENCES audit.audit_findings(finding_id),

    -- Agent hierarchy
    parent_trace_id             UUID        REFERENCES audit.reasoning_traces(trace_id),
    agent_id                    VARCHAR(100),               -- unique agent instance identifier
    agent_type                  VARCHAR(50),
    workflow_node               VARCHAR(100),               -- LangGraph node name
    workflow_step_sequence      INTEGER,                    -- execution order within a run

    -- Model provenance
    model_id                    VARCHAR(100),               -- e.g. claude-opus-4-7
    prompt_template_id          VARCHAR(100),
    prompt_template_version     VARCHAR(20),

    -- Input / Output (immutable once written)
    input_context               JSONB       NOT NULL DEFAULT '{}',
    reasoning_output            TEXT,
    structured_output           JSONB,
    citations                   JSONB       NOT NULL DEFAULT '[]',  -- evidence citations

    -- Quality signals
    confidence_score            NUMERIC(5,4),
    human_review_required       BOOLEAN     NOT NULL DEFAULT FALSE,
    human_review_requested_at   TIMESTAMPTZ,
    human_reviewed_at           TIMESTAMPTZ,
    human_reviewer              VARCHAR(255),
    human_review_notes          TEXT,

    -- Performance telemetry (for cost analysis)
    input_tokens                INTEGER,
    output_tokens               INTEGER,
    cache_read_tokens           INTEGER,    -- prompt cache hit tokens
    cache_write_tokens          INTEGER,    -- prompt cache write tokens
    latency_ms                  INTEGER,

    -- Audit (append-only — no updated_at)
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_trace_agent_type CHECK (
        agent_type IS NULL OR agent_type IN (
            'investigator', 'summarizer', 'prioritizer', 'validator',
            'extractor', 'classifier', 'reporter', 'orchestrator'
        )
    ),
    CONSTRAINT ck_trace_confidence CHECK (
        confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)
    )
);

COMMENT ON TABLE  audit.reasoning_traces                        IS 'Append-only LLM reasoning trace log — full AI auditability for every investigation step';
COMMENT ON COLUMN audit.reasoning_traces.session_id            IS 'UUID grouping all traces from a single agent workflow run or investigation session';
COMMENT ON COLUMN audit.reasoning_traces.parent_trace_id       IS 'Hierarchical chain: orchestrator → sub-agent → validator patterns in multi-agent workflows';
COMMENT ON COLUMN audit.reasoning_traces.workflow_node         IS 'LangGraph node name — enables per-node performance and accuracy analysis';
COMMENT ON COLUMN audit.reasoning_traces.evidence_payload      IS 'see audit.audit_findings — this table stores agent INPUT context, not evidence snapshots';
COMMENT ON COLUMN audit.reasoning_traces.cache_read_tokens     IS 'Anthropic prompt cache hit tokens — used for cost attribution per investigation case';
