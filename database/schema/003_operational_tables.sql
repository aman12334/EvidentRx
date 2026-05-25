-- =============================================================================
-- Script 003: Operational Transaction Tables (ops schema)
--
-- All three core transaction tables are range-partitioned by their primary date
-- column for query performance and data lifecycle management at scale.
--
-- IMPORTANT — composite primary keys on partitioned tables:
--   PostgreSQL requires the partition key to be part of any primary key.
--   purchases  → PK(purchase_id, purchase_date)
--   dispenses  → PK(dispense_id, dispense_date)
--   claims     → PK(claim_id, service_date)
--
-- Cross-partition FK enforcement:
--   PostgreSQL does not enforce FK references *to* partitioned tables from
--   other tables. Where this applies (e.g. ops.claims.dispense_id,
--   audit.audit_findings.purchase_id) the relationship is documented as a
--   "logical FK" in column comments and enforced at the application layer.
-- =============================================================================

-- =============================================================================
-- ops.purchases
-- Purpose     : 340B drug purchases from wholesalers.
-- Partitioned : RANGE on purchase_date — add annual or quarterly partitions
--               before loading data for that period.
-- =============================================================================
CREATE TABLE ops.purchases (
    purchase_id             UUID            NOT NULL DEFAULT gen_random_uuid(),
    purchase_date           DATE            NOT NULL,   -- partition key

    -- Source identity
    external_id             VARCHAR(255),               -- source system record ID

    -- Entity & drug linkage
    covered_entity_id       UUID            NOT NULL REFERENCES ref.covered_entities(ce_id),
    contract_pharmacy_id    UUID            REFERENCES ref.contract_pharmacies(cp_id),
    ndc_11                  VARCHAR(11)     NOT NULL,   -- join key to ref.ndc_drugs
    drug_id                 UUID            REFERENCES ref.ndc_drugs(drug_id),

    -- Wholesaler
    wholesaler_name         TEXT,
    wholesaler_dea          VARCHAR(20),
    invoice_number          VARCHAR(100),
    lot_number              VARCHAR(100),

    -- Quantity & pricing
    quantity                NUMERIC(15,4)   NOT NULL,
    unit_of_measure         VARCHAR(20),
    unit_price              NUMERIC(15,6),
    total_cost              NUMERIC(15,2),
    purchase_price_type     VARCHAR(20),                -- 340B, WAC, GPO, PHS, other
    is_340b_purchase        BOOLEAN         NOT NULL DEFAULT FALSE,
    ceiling_price           NUMERIC(15,6),              -- 340B ceiling price at time of purchase

    -- Ingestion lineage
    source_file             TEXT,
    batch_id                UUID            REFERENCES meta.ingestion_batches(batch_id),
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Composite PK required by partitioning
    PRIMARY KEY (purchase_id, purchase_date),

    CONSTRAINT ck_purchase_price_type CHECK (
        purchase_price_type IS NULL OR
        purchase_price_type IN ('340B', 'WAC', 'GPO', 'PHS', 'other')
    )
) PARTITION BY RANGE (purchase_date);

COMMENT ON TABLE  ops.purchases                  IS '340B drug purchases — range-partitioned by purchase_date';
COMMENT ON COLUMN ops.purchases.ceiling_price    IS '340B ceiling price snapshot at purchase time for financial exposure analysis';
COMMENT ON COLUMN ops.purchases.purchase_id      IS 'Part of composite PK (purchase_id, purchase_date) required by range partitioning';

-- Default catch-all partition for records outside defined ranges
CREATE TABLE ops.purchases_default PARTITION OF ops.purchases DEFAULT;


-- =============================================================================
-- ops.dispenses
-- Purpose     : Drug dispenses at covered entity or contract pharmacy.
--               Patient identifier is stored as a one-way hash — never raw PII.
-- Partitioned : RANGE on dispense_date.
-- =============================================================================
CREATE TABLE ops.dispenses (
    dispense_id             UUID            NOT NULL DEFAULT gen_random_uuid(),
    dispense_date           DATE            NOT NULL,   -- partition key

    -- Source identity
    external_id             VARCHAR(255),

    -- Entity & pharmacy linkage
    covered_entity_id       UUID            NOT NULL REFERENCES ref.covered_entities(ce_id),
    contract_pharmacy_id    UUID            REFERENCES ref.contract_pharmacies(cp_id),

    -- Drug
    ndc_11                  VARCHAR(11)     NOT NULL,
    drug_id                 UUID            REFERENCES ref.ndc_drugs(drug_id),

    -- Privacy-preserving patient identifier
    patient_id_hash         VARCHAR(64)     NOT NULL,   -- SHA-256; never store raw patient ID

    -- Provider
    prescriber_npi          VARCHAR(10),
    prescriber_provider_id  UUID            REFERENCES ref.providers(provider_id),
    dispenser_npi           VARCHAR(10),
    dispenser_provider_id   UUID            REFERENCES ref.providers(provider_id),

    -- Prescription
    rx_number               VARCHAR(50),
    fill_number             SMALLINT        NOT NULL DEFAULT 0,
    written_date            DATE,
    days_supply             SMALLINT,
    quantity                NUMERIC(15,4),
    unit_of_measure         VARCHAR(20),
    dispense_as_written     BOOLEAN,

    -- Payer
    payer_type              VARCHAR(30),                -- medicaid, medicare_part_d, commercial, self_pay, other
    payer_id                VARCHAR(50),
    payer_name              TEXT,

    -- 340B status
    is_340b_dispense        BOOLEAN         NOT NULL DEFAULT FALSE,
    carve_in_election       VARCHAR(20),                -- carve_in, carve_out, not_applicable

    -- Ingestion lineage
    source_file             TEXT,
    batch_id                UUID            REFERENCES meta.ingestion_batches(batch_id),
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    PRIMARY KEY (dispense_id, dispense_date),

    CONSTRAINT ck_dispense_payer_type CHECK (
        payer_type IS NULL OR
        payer_type IN ('medicaid', 'medicare_part_d', 'commercial', 'self_pay', 'other')
    ),
    CONSTRAINT ck_dispense_carve_in CHECK (
        carve_in_election IS NULL OR
        carve_in_election IN ('carve_in', 'carve_out', 'not_applicable')
    )
) PARTITION BY RANGE (dispense_date);

COMMENT ON TABLE  ops.dispenses                     IS 'Drug dispenses — range-partitioned by dispense_date';
COMMENT ON COLUMN ops.dispenses.patient_id_hash     IS 'SHA-256 of source patient identifier — raw PII must never be stored';
COMMENT ON COLUMN ops.dispenses.carve_in_election   IS 'CE election at dispense time — critical for duplicate discount detection';

CREATE TABLE ops.dispenses_default PARTITION OF ops.dispenses DEFAULT;


-- =============================================================================
-- ops.claims
-- Purpose     : Insurance / Medicaid reimbursement claims.
-- Partitioned : RANGE on service_date.
-- Notes       : dispense_id is a logical FK — not enforced by DB because
--               ops.dispenses is partitioned and PG cannot enforce cross-partition
--               FKs from non-partitioned tables.
-- =============================================================================
CREATE TABLE ops.claims (
    claim_id                UUID            NOT NULL DEFAULT gen_random_uuid(),
    service_date            DATE            NOT NULL,   -- partition key

    -- Source identity
    external_id             VARCHAR(255),

    -- Entity linkage
    covered_entity_id       UUID            NOT NULL REFERENCES ref.covered_entities(ce_id),

    -- Logical FK to ops.dispenses — enforced at application layer
    dispense_id             UUID,
    dispense_date           DATE,

    -- Claim attributes
    claim_type              VARCHAR(30)     NOT NULL,   -- medicaid, medicare_part_d, medicare_part_b, commercial, other
    claim_status            VARCHAR(20)     NOT NULL DEFAULT 'submitted',
    payer_id                VARCHAR(50),
    payer_name              TEXT,
    plan_id                 VARCHAR(50),

    -- Patient (hashed)
    patient_id_hash         VARCHAR(64),

    -- Provider
    prescriber_npi          VARCHAR(10),
    dispenser_npi           VARCHAR(10),

    -- Drug
    rx_number               VARCHAR(50),
    ndc_11                  VARCHAR(11)     NOT NULL,
    drug_id                 UUID            REFERENCES ref.ndc_drugs(drug_id),

    -- Dates
    billing_date            DATE,
    paid_date               DATE,

    -- Amounts
    quantity                NUMERIC(15,4),
    days_supply             SMALLINT,
    billed_amount           NUMERIC(15,2),
    allowed_amount          NUMERIC(15,2),
    paid_amount             NUMERIC(15,2),
    patient_pay_amount      NUMERIC(15,2),

    -- Flags
    state_code              CHAR(2),
    is_medicaid             BOOLEAN         NOT NULL DEFAULT FALSE,
    is_340b_billed          BOOLEAN,
    billing_modifier        VARCHAR(10),                -- 'UD' modifier signals 340B to payer

    -- Ingestion lineage
    source_file             TEXT,
    batch_id                UUID            REFERENCES meta.ingestion_batches(batch_id),
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    PRIMARY KEY (claim_id, service_date),

    CONSTRAINT ck_claim_type CHECK (
        claim_type IN ('medicaid', 'medicare_part_d', 'medicare_part_b', 'commercial', 'other')
    ),
    CONSTRAINT ck_claim_status CHECK (
        claim_status IN ('submitted', 'paid', 'denied', 'adjusted', 'reversed', 'void')
    )
) PARTITION BY RANGE (service_date);

COMMENT ON TABLE  ops.claims                    IS 'Reimbursement claims — range-partitioned by service_date';
COMMENT ON COLUMN ops.claims.dispense_id        IS 'Logical FK to ops.dispenses — not DB-enforced due to parent table partitioning';
COMMENT ON COLUMN ops.claims.billing_modifier   IS 'UD modifier indicates 340B drug to payer — required for duplicate discount detection';

CREATE TABLE ops.claims_default PARTITION OF ops.claims DEFAULT;


-- =============================================================================
-- ops.split_billing
-- Purpose     : Links purchase / dispense / claim records for a given patient
--               encounter — the core unit of 340B compliance analysis.
-- Notes       : purchase_id / dispense_id / claim_id are logical FKs stored
--               alongside their partition key date columns for cross-partition
--               queries without FK enforcement.
--               Pre-computed risk flags are set by the deterministic rules engine
--               on ingestion — NOT by LLMs.
-- =============================================================================
CREATE TABLE ops.split_billing (
    split_billing_id            UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Entity & drug
    covered_entity_id           UUID            NOT NULL REFERENCES ref.covered_entities(ce_id),
    ndc_11                      VARCHAR(11)     NOT NULL,
    service_date                DATE            NOT NULL,
    patient_id_hash             VARCHAR(64),

    -- Logical links to partitioned parent tables
    purchase_id                 UUID,
    purchase_date               DATE,           -- needed for partition-aware lookups
    dispense_id                 UUID,
    dispense_date               DATE,
    claim_id                    UUID,
    claim_service_date          DATE,

    -- Split billing attributes
    split_method                VARCHAR(50),    -- accumulator, patient_matching, provider_matching
    carve_in_flag               BOOLEAN,        -- CE election at service date
    is_340b_purchase            BOOLEAN,
    is_medicaid_billed          BOOLEAN,
    accumulator_balance         NUMERIC(15,4),  -- remaining 340B inventory at dispense

    -- Pre-computed risk signals (set by rules engine, NOT AI)
    duplicate_discount_risk     BOOLEAN         NOT NULL DEFAULT FALSE,
    medicaid_overlap_risk       BOOLEAN         NOT NULL DEFAULT FALSE,
    carve_out_violation_risk    BOOLEAN         NOT NULL DEFAULT FALSE,
    ineligible_patient_risk     BOOLEAN         NOT NULL DEFAULT FALSE,
    risk_score                  NUMERIC(5,4),   -- composite 0-1 score set by rules engine

    -- Ingestion lineage
    source_file                 TEXT,
    batch_id                    UUID            REFERENCES meta.ingestion_batches(batch_id),
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_risk_score CHECK (risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 1))
);

COMMENT ON TABLE  ops.split_billing                          IS 'Split billing records — core unit of 340B compliance analysis linking purchase/dispense/claim';
COMMENT ON COLUMN ops.split_billing.risk_score               IS 'Composite risk score 0-1 computed deterministically by rules engine before AI analysis';
COMMENT ON COLUMN ops.split_billing.accumulator_balance      IS '340B inventory accumulator balance at time of dispense — used in split billing calculations';
COMMENT ON COLUMN ops.split_billing.duplicate_discount_risk  IS 'TRUE when same drug purchased at 340B price AND Medicaid billed — set by rules engine';
