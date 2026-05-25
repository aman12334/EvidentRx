-- =============================================================================
-- Script 002: Reference Tables (meta + ref schemas)
--
-- Creation order (dependency-safe):
--   meta.ingestion_batches
--   ref.covered_entities
--   ref.contract_pharmacies
--   ref.medicaid_exclusions
--   ref.providers
--   ref.provider_taxonomies
--   ref.ndc_drugs
-- =============================================================================

-- =============================================================================
-- meta.ingestion_batches
-- Purpose : Tracks every data ingestion job — primary lineage anchor for all
--           reference and operational records.
-- Notes   : Created first because other tables reference it via batch_id.
-- =============================================================================
CREATE TABLE meta.ingestion_batches (
    batch_id            UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_name          VARCHAR(255),
    source_type         VARCHAR(50)     NOT NULL,
    source_file         TEXT            NOT NULL,
    source_file_hash    VARCHAR(64),                -- SHA-256 hex digest
    file_size_bytes     BIGINT,
    record_count        INTEGER,
    records_processed   INTEGER         NOT NULL DEFAULT 0,
    records_failed      INTEGER         NOT NULL DEFAULT 0,
    status              VARCHAR(20)     NOT NULL DEFAULT 'pending',
    error_details       JSONB,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_ingestion_status CHECK (
        status IN ('pending', 'processing', 'completed', 'failed', 'partial')
    ),
    CONSTRAINT ck_ingestion_source_type CHECK (
        source_type IN (
            'hrsa_ce', 'hrsa_cp', 'medicaid_exclusion',
            'nppes', 'ndc_fda',
            'purchases', 'dispenses', 'claims', 'split_billing',
            'other'
        )
    )
);

COMMENT ON TABLE  meta.ingestion_batches                  IS 'Every data ingestion job — primary lineage anchor for all imported records';
COMMENT ON COLUMN meta.ingestion_batches.source_file_hash IS 'SHA-256 digest of source file — used for idempotent reloads';
COMMENT ON COLUMN meta.ingestion_batches.source_type      IS 'Enumerated source system identifier';


-- =============================================================================
-- ref.covered_entities
-- Purpose     : HRSA-registered 340B covered entities.
-- Temporal    : SCD Type 2 — valid_from / valid_to / is_current track history.
-- Scalability : Indexed on hrsa_id, npi, state_code, entity_name (trigram).
--               Partial unique index enforces one current row per hrsa_id.
-- =============================================================================
CREATE TABLE ref.covered_entities (
    ce_id                       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- HRSA identity
    hrsa_id                     VARCHAR(20) NOT NULL,
    entity_name                 TEXT        NOT NULL,
    entity_type_code            VARCHAR(20),            -- DSH, CAH, FQHC, RHC, etc.
    entity_type_description     TEXT,

    -- Location
    street_address              TEXT,
    city                        VARCHAR(100),
    state_code                  CHAR(2),
    zip_code                    VARCHAR(10),
    county                      VARCHAR(100),

    -- Provider identifiers
    npi                         VARCHAR(10),
    primary_340b_program        VARCHAR(50),
    outpatient_facility_name    TEXT,
    parent_site_name            TEXT,
    grantee_number              VARCHAR(50),

    -- Program dates and status
    program_participation_start DATE,
    program_termination_date    DATE,
    program_status              VARCHAR(20) NOT NULL DEFAULT 'Active',
    is_active                   BOOLEAN     NOT NULL DEFAULT TRUE,

    -- SCD Type 2 temporal validity
    valid_from                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to                    TIMESTAMPTZ,            -- NULL means currently active
    is_current                  BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Ingestion lineage
    source_file                 TEXT,
    source_file_hash            VARCHAR(64),
    batch_id                    UUID        REFERENCES meta.ingestion_batches(batch_id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  ref.covered_entities          IS 'HRSA 340B covered entities — SCD Type 2 with full history';
COMMENT ON COLUMN ref.covered_entities.hrsa_id  IS 'HRSA-assigned covered entity identifier (e.g. 060000300)';
COMMENT ON COLUMN ref.covered_entities.is_current IS 'TRUE for the active version only — enforced by partial unique index';
COMMENT ON COLUMN ref.covered_entities.valid_to IS 'NULL on the current row; set when superseded by a new SCD version';


-- =============================================================================
-- ref.contract_pharmacies
-- Purpose     : 340B contract pharmacy registrations per covered entity.
-- Temporal    : SCD Type 2.
-- Scalability : Indexed on covered_entity_id, pharmacy_npi, state_code.
-- =============================================================================
CREATE TABLE ref.contract_pharmacies (
    cp_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Relationships
    covered_entity_id   UUID        NOT NULL REFERENCES ref.covered_entities(ce_id),
    hrsa_id             VARCHAR(20) NOT NULL,           -- denormalized CE HRSA ID for fast lookups

    -- Pharmacy identity
    pharmacy_name       TEXT        NOT NULL,
    pharmacy_npi        VARCHAR(10),
    pharmacy_ncpdp      VARCHAR(7),                     -- NCPDP Provider ID
    chain_name          TEXT,
    pharmacy_type       VARCHAR(50),                    -- retail, specialty, LTC, mail_order, etc.

    -- Location
    street_address      TEXT,
    city                VARCHAR(100),
    state_code          CHAR(2),
    zip_code            VARCHAR(10),

    -- Registration
    registration_date   DATE,
    termination_date    DATE,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,

    -- SCD Type 2
    valid_from          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to            TIMESTAMPTZ,
    is_current          BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Ingestion lineage
    source_file         TEXT,
    batch_id            UUID        REFERENCES meta.ingestion_batches(batch_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  ref.contract_pharmacies                         IS '340B contract pharmacy registrations — SCD Type 2';
COMMENT ON COLUMN ref.contract_pharmacies.hrsa_id                IS 'Denormalized CE HRSA ID for join performance without traversing SCD history';
COMMENT ON COLUMN ref.contract_pharmacies.pharmacy_ncpdp         IS 'NCPDP 7-digit provider ID — key for pharmacy claim matching';


-- =============================================================================
-- ref.medicaid_exclusions
-- Purpose     : Covered entity Medicaid carve-in / carve-out elections by period.
--               Sourced from HRSA quarterly Medicaid exclusion files.
-- Temporal    : Period-based (filing_period, period_start/period_end) rather
--               than SCD — each quarterly file creates new records.
-- Scalability : Indexed on (covered_entity_id, period_start, period_end)
--               for temporal range lookups during compliance checks.
-- =============================================================================
CREATE TABLE ref.medicaid_exclusions (
    exclusion_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Entity linkage
    covered_entity_id   UUID        REFERENCES ref.covered_entities(ce_id),
    hrsa_id             VARCHAR(20) NOT NULL,           -- denormalized; CE may not yet be loaded

    -- Exclusion attributes
    state_code          CHAR(2)     NOT NULL,
    exclusion_type      VARCHAR(20) NOT NULL,           -- carve_in, carve_out, not_elected
    carve_type_detail   TEXT,                           -- source-specific detail field

    -- Temporal period
    filing_period       VARCHAR(10) NOT NULL,           -- e.g. '2025Q4'
    period_start        DATE        NOT NULL,
    period_end          DATE,                           -- NULL = open-ended current period
    is_current          BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Ingestion lineage
    source_file         TEXT,
    batch_id            UUID        REFERENCES meta.ingestion_batches(batch_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_exclusion_type CHECK (exclusion_type IN ('carve_in', 'carve_out', 'not_elected'))
);

COMMENT ON TABLE  ref.medicaid_exclusions                   IS 'Quarterly Medicaid carve-in / carve-out elections from HRSA exclusion files';
COMMENT ON COLUMN ref.medicaid_exclusions.filing_period     IS 'Quarter identifier e.g. 2025Q4 — maps to a specific HRSA file load';
COMMENT ON COLUMN ref.medicaid_exclusions.exclusion_type    IS 'carve_in = Medicaid billed through 340B; carve_out = excluded from 340B';


-- =============================================================================
-- ref.providers
-- Purpose     : NPPES provider registry — individual and organization providers.
-- Temporal    : SCD Type 2 via weekly NPPES dissemination files.
-- Scalability : Indexed on npi (partial unique for current), state_code.
--               Taxonomy codes are in a separate child table.
-- Notes       : NPPES dataset is ~8M records; partition if needed in future.
-- =============================================================================
CREATE TABLE ref.providers (
    provider_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- NPI identity
    npi                     VARCHAR(10) NOT NULL,
    entity_type_code        CHAR(1)     NOT NULL,       -- '1'=Individual, '2'=Organization

    -- Individual name fields
    provider_last_name      VARCHAR(100),
    provider_first_name     VARCHAR(100),
    provider_middle_name    VARCHAR(100),
    provider_credential     VARCHAR(50),                -- MD, DO, NP, PA, RPh, etc.

    -- Organization fields
    organization_name       TEXT,
    doing_business_as       TEXT,

    -- Practice location (primary)
    street_address          TEXT,
    city                    VARCHAR(100),
    state_code              CHAR(2),
    zip_code                VARCHAR(10),
    phone                   VARCHAR(20),

    -- NPPES status
    enumeration_date        DATE,
    last_update_date        DATE,
    deactivation_date       DATE,
    deactivation_reason     VARCHAR(2),
    reactivation_date       DATE,
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,

    -- SCD Type 2
    valid_from              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to                TIMESTAMPTZ,
    is_current              BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Source tracking
    source_week             VARCHAR(20),                -- e.g. '051126_051726' from filename
    source_file             TEXT,
    batch_id                UUID        REFERENCES meta.ingestion_batches(batch_id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_provider_entity_type CHECK (entity_type_code IN ('1', '2'))
);

COMMENT ON TABLE  ref.providers                     IS 'NPPES provider registry — SCD Type 2 from weekly dissemination files';
COMMENT ON COLUMN ref.providers.entity_type_code    IS '1=Individual provider, 2=Organization';
COMMENT ON COLUMN ref.providers.source_week         IS 'Week range from NPPES filename — tracks which weekly file introduced this record';


-- =============================================================================
-- ref.provider_taxonomies
-- Purpose     : NPPES taxonomy codes — multi-valued per provider (up to 15).
-- Notes       : Child table to keep ref.providers lean.
-- =============================================================================
CREATE TABLE ref.provider_taxonomies (
    taxonomy_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id             UUID        NOT NULL REFERENCES ref.providers(provider_id) ON DELETE CASCADE,
    taxonomy_code           VARCHAR(20) NOT NULL,
    taxonomy_description    TEXT,
    license_number          VARCHAR(50),
    license_state           CHAR(2),
    is_primary              BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ref.provider_taxonomies IS 'NPPES taxonomy codes — up to 15 per provider in a child table';


-- =============================================================================
-- ref.ndc_drugs
-- Purpose     : FDA NDC drug directory — canonical drug reference.
-- Notes       : ndc_11 is the normalized 11-digit zero-padded NDC (5-4-2 format,
--               no hyphens). All operational tables use ndc_11 as the join key.
-- Scalability : Unique index on ndc_11; trigram index on nonproprietary_name.
-- =============================================================================
CREATE TABLE ref.ndc_drugs (
    drug_id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- NDC identifiers
    ndc_11                  VARCHAR(11) NOT NULL UNIQUE, -- canonical 11-digit normalized
    ndc_raw                 VARCHAR(20),                 -- as received from FDA (may have hyphens)
    application_number      VARCHAR(20),                 -- NDA/ANDA/BLA number
    product_ndc             VARCHAR(12),
    package_ndc             VARCHAR(12),
    labeler_code            VARCHAR(5),
    product_code            VARCHAR(4),
    package_code            VARCHAR(2),

    -- Drug identity
    proprietary_name        TEXT,
    proprietary_name_suffix TEXT,
    nonproprietary_name     TEXT,
    labeler_name            TEXT,
    substance_name          TEXT,
    strength                TEXT,
    dosage_form             VARCHAR(100),
    route                   TEXT,

    -- Classification
    marketing_category      VARCHAR(100),
    application_type        VARCHAR(50),
    product_type            VARCHAR(50),
    dea_schedule            VARCHAR(10),

    -- Lifecycle
    listing_expiration_date DATE,
    marketing_start_date    DATE,
    marketing_end_date      DATE,
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Ingestion lineage
    source_file             TEXT,
    batch_id                UUID        REFERENCES meta.ingestion_batches(batch_id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  ref.ndc_drugs         IS 'FDA NDC drug directory — ndc_11 is the canonical join key used across all operational tables';
COMMENT ON COLUMN ref.ndc_drugs.ndc_11  IS '11-digit zero-padded NDC in 5-4-2 format without hyphens (e.g. 00069306060)';
