-- ============================================================================
-- Phase 10: Interoperability Infrastructure Tables
-- ============================================================================
-- Schema: interop
--
-- Tables
-- ──────
--   interop.connector_configs    — registered EHR / pharmacy connector configs
--   interop.ingestion_jobs       — sync job run history (start / finish / counts)
--   interop.source_lineage       — transformation lineage per canonical record
--   interop.hl7_dead_letters     — dead-letter queue for malformed HL7 messages
--   interop.sync_cursors         — incremental sync cursor checkpoints
--
-- Retention
-- ─────────
--   All tables carry a created_at timestamp.
--   source_lineage is retention-tagged for 7-year HRSA compliance.
-- ============================================================================

BEGIN;

-- ── Schema ────────────────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS interop;

-- ── connector_configs ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS interop.connector_configs (
    connector_id        TEXT                        NOT NULL,
    tenant_id           UUID                        NOT NULL
                            REFERENCES ref.covered_entities(covered_entity_id)
                            ON DELETE CASCADE,
    source_type         TEXT                        NOT NULL,
    -- 'fhir' | 'hl7v2' | 'x12_837p' | 'ncpdp_batch' | 'pbm_api' | …
    vendor              TEXT                        NOT NULL DEFAULT 'generic',
    display_name        TEXT                        NOT NULL,
    base_url            TEXT,
    auth_type           TEXT                        NOT NULL DEFAULT 'bearer',
    timeout_sec         INTEGER                     NOT NULL DEFAULT 30,
    max_retries         INTEGER                     NOT NULL DEFAULT 3,
    page_size           INTEGER                     NOT NULL DEFAULT 200,
    resource_types      TEXT[]                      NOT NULL DEFAULT '{}',
    -- Secrets stored as references only — resolved via secrets manager
    secret_ref          TEXT,
    -- Arbitrary connector-specific config (non-secret)
    extra               JSONB                       NOT NULL DEFAULT '{}',
    is_active           BOOLEAN                     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ                 NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ                 NOT NULL DEFAULT NOW(),

    PRIMARY KEY (connector_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_connector_configs_tenant
    ON interop.connector_configs (tenant_id)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_connector_configs_source_type
    ON interop.connector_configs (source_type);

COMMENT ON TABLE interop.connector_configs IS
    'Registered healthcare data connector configurations per tenant. '
    'Secrets are stored as references and resolved at runtime by the secrets manager.';

-- ── ingestion_jobs ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS interop.ingestion_jobs (
    job_id              UUID                        NOT NULL DEFAULT gen_random_uuid(),
    connector_id        TEXT                        NOT NULL,
    tenant_id           UUID                        NOT NULL,
    resource_type       TEXT                        NOT NULL,
    source_system       TEXT                        NOT NULL,
    ingest_mode         TEXT                        NOT NULL DEFAULT 'incremental',
    -- 'full_load' | 'incremental' | 'change_capture' | 'replay'
    status              TEXT                        NOT NULL DEFAULT 'running',
    -- 'running' | 'completed' | 'failed' | 'partial'
    started_at          TIMESTAMPTZ                 NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    records_fetched     INTEGER                     NOT NULL DEFAULT 0,
    records_written     INTEGER                     NOT NULL DEFAULT 0,
    records_failed      INTEGER                     NOT NULL DEFAULT 0,
    records_duplicate   INTEGER                     NOT NULL DEFAULT 0,
    cursor_start        TEXT,
    cursor_end          TEXT,
    error_summary       TEXT,
    duration_seconds    NUMERIC(10,3)
                            GENERATED ALWAYS AS (
                                CASE
                                    WHEN finished_at IS NOT NULL
                                    THEN EXTRACT(EPOCH FROM (finished_at - started_at))
                                    ELSE NULL
                                END
                            ) STORED,

    PRIMARY KEY (job_id)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_connector
    ON interop.ingestion_jobs (connector_id, tenant_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_tenant_status
    ON interop.ingestion_jobs (tenant_id, status, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_started_at
    ON interop.ingestion_jobs (started_at DESC);

COMMENT ON TABLE interop.ingestion_jobs IS
    'Audit record of every sync job execution. '
    'One row per (connector, resource_type, run). '
    'Retained 7 years per HRSA 340B audit requirements.';

-- ── source_lineage ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS interop.source_lineage (
    lineage_id          UUID                        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id           UUID                        NOT NULL,
    source_system       TEXT                        NOT NULL,
    resource_type       TEXT                        NOT NULL,
    canonical_type      TEXT,
    checksum            TEXT,                       -- SHA-256 of canonical JSON
    transformation_steps JSONB                     NOT NULL DEFAULT '[]',
    raw_ref             TEXT,                       -- reference to raw record storage
    canonical_ref       TEXT,                       -- reference to persisted canonical
    is_valid            BOOLEAN                     NOT NULL DEFAULT TRUE,
    error_summary       TEXT,
    created_at          TIMESTAMPTZ                 NOT NULL DEFAULT NOW(),

    PRIMARY KEY (lineage_id)
);

CREATE INDEX IF NOT EXISTS idx_source_lineage_tenant
    ON interop.source_lineage (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_source_lineage_checksum
    ON interop.source_lineage (checksum)
    WHERE checksum IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_source_lineage_canonical_type
    ON interop.source_lineage (canonical_type, tenant_id);

COMMENT ON TABLE interop.source_lineage IS
    'Full transformation lineage for every canonical record. '
    'Records every processing step from raw source to persisted canonical. '
    'Retained 7 years per HRSA 340B audit requirements.';

-- ── hl7_dead_letters ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS interop.hl7_dead_letters (
    dlq_id              UUID                        NOT NULL DEFAULT gen_random_uuid(),
    tenant_id           UUID                        NOT NULL,
    reason              TEXT                        NOT NULL,
    -- 'parse_error' | 'normalisation_error' | 'validation_error'
    -- | 'duplicate' | 'unsupported_type' | 'downstream_failure'
    raw_message         TEXT                        NOT NULL,
    message_type        TEXT                        NOT NULL DEFAULT 'UNKNOWN',
    trigger_event       TEXT                        NOT NULL DEFAULT '',
    message_id          TEXT                        NOT NULL DEFAULT '',
    sending_facility    TEXT                        NOT NULL DEFAULT '',
    parse_errors        TEXT[]                      NOT NULL DEFAULT '{}',
    detail              TEXT                        NOT NULL DEFAULT '',
    tags                JSONB                       NOT NULL DEFAULT '{}',
    replayed            BOOLEAN                     NOT NULL DEFAULT FALSE,
    replay_count        INTEGER                     NOT NULL DEFAULT 0,
    enqueued_at         TIMESTAMPTZ                 NOT NULL DEFAULT NOW(),
    replayed_at         TIMESTAMPTZ,

    PRIMARY KEY (dlq_id)
);

CREATE INDEX IF NOT EXISTS idx_hl7_dlq_tenant
    ON interop.hl7_dead_letters (tenant_id, enqueued_at DESC);

CREATE INDEX IF NOT EXISTS idx_hl7_dlq_unprocessed
    ON interop.hl7_dead_letters (tenant_id, reason)
    WHERE replayed = FALSE;

COMMENT ON TABLE interop.hl7_dead_letters IS
    'Dead-letter queue for HL7 v2 messages that could not be parsed or normalised. '
    'Entries are available for manual inspection and replay.';

-- ── sync_cursors ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS interop.sync_cursors (
    connector_id        TEXT                        NOT NULL,
    tenant_id           UUID                        NOT NULL,
    resource_type       TEXT                        NOT NULL,
    last_value          TEXT,                       -- ISO timestamp or page token
    last_synced         TIMESTAMPTZ,
    records_total       BIGINT                      NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ                 NOT NULL DEFAULT NOW(),

    PRIMARY KEY (connector_id, tenant_id, resource_type)
);

COMMENT ON TABLE interop.sync_cursors IS
    'Incremental sync checkpoint per (connector, tenant, resource_type). '
    'Enables restarts to resume exactly where they left off.';

-- ── Trigger: updated_at on connector_configs ──────────────────────────────────

CREATE OR REPLACE FUNCTION interop.touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_connector_configs_updated_at'
    ) THEN
        CREATE TRIGGER trg_connector_configs_updated_at
        BEFORE UPDATE ON interop.connector_configs
        FOR EACH ROW EXECUTE FUNCTION interop.touch_updated_at();
    END IF;
END $$;

COMMIT;
