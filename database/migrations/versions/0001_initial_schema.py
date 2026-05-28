"""Initial schema — all four namespaces (meta, ref, ops, audit)

Revision ID: 0001
Revises:
Create Date: 2026-05-23

Applies the complete platform schema in dependency order:
  1. Extensions and schemas
  2. meta.ingestion_batches
  3. ref.*  (covered_entities, contract_pharmacies, medicaid_exclusions,
              providers, provider_taxonomies, ndc_drugs)
  4. ops.*  (purchases, dispenses, claims, split_billing — partitioned)
  5. audit.* (compliance_rules, investigation_cases, audit_findings,
               reasoning_traces)
  6. All indexes
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Extensions and schema namespaces
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute("CREATE SCHEMA IF NOT EXISTS ref")
    op.execute("CREATE SCHEMA IF NOT EXISTS ops")
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")
    op.execute("CREATE SCHEMA IF NOT EXISTS meta")

    # ------------------------------------------------------------------
    # 2. meta.ingestion_batches
    # ------------------------------------------------------------------
    op.create_table(
        "ingestion_batches",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_name", sa.String(255)),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_file", sa.Text, nullable=False),
        sa.Column("source_file_hash", sa.String(64)),
        sa.Column("file_size_bytes", sa.BigInteger),
        sa.Column("record_count", sa.Integer),
        sa.Column("records_processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_details", postgresql.JSONB),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('pending','processing','completed','failed','partial')",
            name="ck_ingestion_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('hrsa_ce','hrsa_cp','medicaid_exclusion','nppes',"
            "'ndc_fda','purchases','dispenses','claims','split_billing','other')",
            name="ck_ingestion_source_type",
        ),
        schema="meta",
    )

    # ------------------------------------------------------------------
    # 3. ref.covered_entities
    # ------------------------------------------------------------------
    op.create_table(
        "covered_entities",
        sa.Column("ce_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("hrsa_id", sa.String(20), nullable=False),
        sa.Column("entity_name", sa.Text, nullable=False),
        sa.Column("entity_type_code", sa.String(20)),
        sa.Column("entity_type_description", sa.Text),
        sa.Column("street_address", sa.Text),
        sa.Column("city", sa.String(100)),
        sa.Column("state_code", sa.String(2)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("county", sa.String(100)),
        sa.Column("npi", sa.String(10)),
        sa.Column("primary_340b_program", sa.String(50)),
        sa.Column("outpatient_facility_name", sa.Text),
        sa.Column("parent_site_name", sa.Text),
        sa.Column("grantee_number", sa.String(50)),
        sa.Column("program_participation_start", sa.Date),
        sa.Column("program_termination_date", sa.Date),
        sa.Column("program_status", sa.String(20), nullable=False, server_default="Active"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("source_file", sa.Text),
        sa.Column("source_file_hash", sa.String(64)),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meta.ingestion_batches.batch_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="ref",
    )

    # ------------------------------------------------------------------
    # 4. ref.contract_pharmacies
    # ------------------------------------------------------------------
    op.create_table(
        "contract_pharmacies",
        sa.Column("cp_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("covered_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ref.covered_entities.ce_id"), nullable=False),
        sa.Column("hrsa_id", sa.String(20), nullable=False),
        sa.Column("pharmacy_name", sa.Text, nullable=False),
        sa.Column("pharmacy_npi", sa.String(10)),
        sa.Column("pharmacy_ncpdp", sa.String(7)),
        sa.Column("chain_name", sa.Text),
        sa.Column("pharmacy_type", sa.String(50)),
        sa.Column("street_address", sa.Text),
        sa.Column("city", sa.String(100)),
        sa.Column("state_code", sa.String(2)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("registration_date", sa.Date),
        sa.Column("termination_date", sa.Date),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("source_file", sa.Text),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meta.ingestion_batches.batch_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="ref",
    )

    # ------------------------------------------------------------------
    # 5. ref.medicaid_exclusions
    # ------------------------------------------------------------------
    op.create_table(
        "medicaid_exclusions",
        sa.Column("exclusion_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("covered_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ref.covered_entities.ce_id")),
        sa.Column("hrsa_id", sa.String(20), nullable=False),
        sa.Column("state_code", sa.String(2), nullable=False),
        sa.Column("exclusion_type", sa.String(20), nullable=False),
        sa.Column("carve_type_detail", sa.Text),
        sa.Column("filing_period", sa.String(10), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("source_file", sa.Text),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meta.ingestion_batches.batch_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "exclusion_type IN ('carve_in','carve_out','not_elected')",
            name="ck_exclusion_type",
        ),
        schema="ref",
    )

    # ------------------------------------------------------------------
    # 6. ref.providers
    # ------------------------------------------------------------------
    op.create_table(
        "providers",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("npi", sa.String(10), nullable=False),
        sa.Column("entity_type_code", sa.String(1), nullable=False),
        sa.Column("provider_last_name", sa.String(100)),
        sa.Column("provider_first_name", sa.String(100)),
        sa.Column("provider_middle_name", sa.String(100)),
        sa.Column("provider_credential", sa.String(50)),
        sa.Column("organization_name", sa.Text),
        sa.Column("doing_business_as", sa.Text),
        sa.Column("street_address", sa.Text),
        sa.Column("city", sa.String(100)),
        sa.Column("state_code", sa.String(2)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("phone", sa.String(20)),
        sa.Column("enumeration_date", sa.Date),
        sa.Column("last_update_date", sa.Date),
        sa.Column("deactivation_date", sa.Date),
        sa.Column("deactivation_reason", sa.String(2)),
        sa.Column("reactivation_date", sa.Date),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("source_week", sa.String(20)),
        sa.Column("source_file", sa.Text),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meta.ingestion_batches.batch_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("entity_type_code IN ('1','2')", name="ck_provider_entity_type"),
        schema="ref",
    )

    # ------------------------------------------------------------------
    # 7. ref.provider_taxonomies
    # ------------------------------------------------------------------
    op.create_table(
        "provider_taxonomies",
        sa.Column("taxonomy_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ref.providers.provider_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("taxonomy_code", sa.String(20), nullable=False),
        sa.Column("taxonomy_description", sa.Text),
        sa.Column("license_number", sa.String(50)),
        sa.Column("license_state", sa.String(2)),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="ref",
    )

    # ------------------------------------------------------------------
    # 8. ref.ndc_drugs
    # ------------------------------------------------------------------
    op.create_table(
        "ndc_drugs",
        sa.Column("drug_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("ndc_11", sa.String(11), nullable=False, unique=True),
        sa.Column("ndc_raw", sa.String(20)),
        sa.Column("application_number", sa.String(20)),
        sa.Column("product_ndc", sa.String(12)),
        sa.Column("package_ndc", sa.String(12)),
        sa.Column("labeler_code", sa.String(5)),
        sa.Column("product_code", sa.String(4)),
        sa.Column("package_code", sa.String(2)),
        sa.Column("proprietary_name", sa.Text),
        sa.Column("proprietary_name_suffix", sa.Text),
        sa.Column("nonproprietary_name", sa.Text),
        sa.Column("labeler_name", sa.Text),
        sa.Column("substance_name", sa.Text),
        sa.Column("strength", sa.Text),
        sa.Column("dosage_form", sa.String(100)),
        sa.Column("route", sa.Text),
        sa.Column("marketing_category", sa.String(100)),
        sa.Column("application_type", sa.String(50)),
        sa.Column("product_type", sa.String(50)),
        sa.Column("dea_schedule", sa.String(10)),
        sa.Column("listing_expiration_date", sa.Date),
        sa.Column("marketing_start_date", sa.Date),
        sa.Column("marketing_end_date", sa.Date),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("source_file", sa.Text),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meta.ingestion_batches.batch_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="ref",
    )

    # ------------------------------------------------------------------
    # 9. ops.purchases  (partitioned — use raw DDL)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE ops.purchases (
            purchase_id         UUID            NOT NULL DEFAULT gen_random_uuid(),
            purchase_date       DATE            NOT NULL,
            external_id         VARCHAR(255),
            covered_entity_id   UUID            NOT NULL REFERENCES ref.covered_entities(ce_id),
            contract_pharmacy_id UUID           REFERENCES ref.contract_pharmacies(cp_id),
            ndc_11              VARCHAR(11)     NOT NULL,
            drug_id             UUID            REFERENCES ref.ndc_drugs(drug_id),
            wholesaler_name     TEXT,
            wholesaler_dea      VARCHAR(20),
            invoice_number      VARCHAR(100),
            lot_number          VARCHAR(100),
            quantity            NUMERIC(15,4)   NOT NULL,
            unit_of_measure     VARCHAR(20),
            unit_price          NUMERIC(15,6),
            total_cost          NUMERIC(15,2),
            purchase_price_type VARCHAR(20),
            is_340b_purchase    BOOLEAN         NOT NULL DEFAULT FALSE,
            ceiling_price       NUMERIC(15,6),
            source_file         TEXT,
            batch_id            UUID            REFERENCES meta.ingestion_batches(batch_id),
            created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            PRIMARY KEY (purchase_id, purchase_date),
            CONSTRAINT ck_purchase_price_type CHECK (
                purchase_price_type IS NULL OR
                purchase_price_type IN ('340B','WAC','GPO','PHS','other')
            )
        ) PARTITION BY RANGE (purchase_date)
    """)
    op.execute(
        "CREATE TABLE ops.purchases_default PARTITION OF ops.purchases DEFAULT"
    )

    # ------------------------------------------------------------------
    # 10. ops.dispenses  (partitioned)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE ops.dispenses (
            dispense_id             UUID            NOT NULL DEFAULT gen_random_uuid(),
            dispense_date           DATE            NOT NULL,
            external_id             VARCHAR(255),
            covered_entity_id       UUID            NOT NULL REFERENCES ref.covered_entities(ce_id),
            contract_pharmacy_id    UUID            REFERENCES ref.contract_pharmacies(cp_id),
            ndc_11                  VARCHAR(11)     NOT NULL,
            drug_id                 UUID            REFERENCES ref.ndc_drugs(drug_id),
            patient_id_hash         VARCHAR(64)     NOT NULL,
            prescriber_npi          VARCHAR(10),
            prescriber_provider_id  UUID            REFERENCES ref.providers(provider_id),
            dispenser_npi           VARCHAR(10),
            dispenser_provider_id   UUID            REFERENCES ref.providers(provider_id),
            rx_number               VARCHAR(50),
            fill_number             SMALLINT        NOT NULL DEFAULT 0,
            written_date            DATE,
            days_supply             SMALLINT,
            quantity                NUMERIC(15,4),
            unit_of_measure         VARCHAR(20),
            dispense_as_written     BOOLEAN,
            payer_type              VARCHAR(30),
            payer_id                VARCHAR(50),
            payer_name              TEXT,
            is_340b_dispense        BOOLEAN         NOT NULL DEFAULT FALSE,
            carve_in_election       VARCHAR(20),
            source_file             TEXT,
            batch_id                UUID            REFERENCES meta.ingestion_batches(batch_id),
            created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            PRIMARY KEY (dispense_id, dispense_date),
            CONSTRAINT ck_dispense_payer_type CHECK (
                payer_type IS NULL OR
                payer_type IN ('medicaid','medicare_part_d','commercial','self_pay','other')
            ),
            CONSTRAINT ck_dispense_carve_in CHECK (
                carve_in_election IS NULL OR
                carve_in_election IN ('carve_in','carve_out','not_applicable')
            )
        ) PARTITION BY RANGE (dispense_date)
    """)
    op.execute(
        "CREATE TABLE ops.dispenses_default PARTITION OF ops.dispenses DEFAULT"
    )

    # ------------------------------------------------------------------
    # 11. ops.claims  (partitioned)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE ops.claims (
            claim_id            UUID            NOT NULL DEFAULT gen_random_uuid(),
            service_date        DATE            NOT NULL,
            external_id         VARCHAR(255),
            covered_entity_id   UUID            NOT NULL REFERENCES ref.covered_entities(ce_id),
            dispense_id         UUID,
            dispense_date       DATE,
            claim_type          VARCHAR(30)     NOT NULL,
            claim_status        VARCHAR(20)     NOT NULL DEFAULT 'submitted',
            payer_id            VARCHAR(50),
            payer_name          TEXT,
            plan_id             VARCHAR(50),
            patient_id_hash     VARCHAR(64),
            prescriber_npi      VARCHAR(10),
            dispenser_npi       VARCHAR(10),
            rx_number           VARCHAR(50),
            ndc_11              VARCHAR(11)     NOT NULL,
            drug_id             UUID            REFERENCES ref.ndc_drugs(drug_id),
            billing_date        DATE,
            paid_date           DATE,
            quantity            NUMERIC(15,4),
            days_supply         SMALLINT,
            billed_amount       NUMERIC(15,2),
            allowed_amount      NUMERIC(15,2),
            paid_amount         NUMERIC(15,2),
            patient_pay_amount  NUMERIC(15,2),
            state_code          CHAR(2),
            is_medicaid         BOOLEAN         NOT NULL DEFAULT FALSE,
            is_340b_billed      BOOLEAN,
            billing_modifier    VARCHAR(10),
            source_file         TEXT,
            batch_id            UUID            REFERENCES meta.ingestion_batches(batch_id),
            created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            PRIMARY KEY (claim_id, service_date),
            CONSTRAINT ck_claim_type CHECK (
                claim_type IN ('medicaid','medicare_part_d','medicare_part_b','commercial','other')
            ),
            CONSTRAINT ck_claim_status CHECK (
                claim_status IN ('submitted','paid','denied','adjusted','reversed','void')
            )
        ) PARTITION BY RANGE (service_date)
    """)
    op.execute(
        "CREATE TABLE ops.claims_default PARTITION OF ops.claims DEFAULT"
    )

    # ------------------------------------------------------------------
    # 12. ops.split_billing
    # ------------------------------------------------------------------
    op.create_table(
        "split_billing",
        sa.Column("split_billing_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("covered_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ref.covered_entities.ce_id"), nullable=False),
        sa.Column("ndc_11", sa.String(11), nullable=False),
        sa.Column("service_date", sa.Date, nullable=False),
        sa.Column("patient_id_hash", sa.String(64)),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True)),
        sa.Column("purchase_date", sa.Date),
        sa.Column("dispense_id", postgresql.UUID(as_uuid=True)),
        sa.Column("dispense_date", sa.Date),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True)),
        sa.Column("claim_service_date", sa.Date),
        sa.Column("split_method", sa.String(50)),
        sa.Column("carve_in_flag", sa.Boolean),
        sa.Column("is_340b_purchase", sa.Boolean),
        sa.Column("is_medicaid_billed", sa.Boolean),
        sa.Column("accumulator_balance", sa.Numeric(15, 4)),
        sa.Column("duplicate_discount_risk", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("medicaid_overlap_risk", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("carve_out_violation_risk", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("ineligible_patient_risk", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("risk_score", sa.Numeric(5, 4)),
        sa.Column("source_file", sa.Text),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meta.ingestion_batches.batch_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 1)",
            name="ck_risk_score",
        ),
        schema="ops",
    )

    # ------------------------------------------------------------------
    # 13. audit.compliance_rules
    # ------------------------------------------------------------------
    op.create_table(
        "compliance_rules",
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rule_code", sa.String(50), nullable=False, unique=True),
        sa.Column("rule_name", sa.String(255), nullable=False),
        sa.Column("rule_category", sa.String(50), nullable=False),
        sa.Column("rule_version", sa.String(20), nullable=False, server_default="1.0.0"),
        sa.Column("parent_rule_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("audit.compliance_rules.rule_id")),
        sa.Column("description", sa.Text),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("logic_definition", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("regulatory_reference", sa.Text),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("expiration_date", sa.Date),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "severity IN ('critical','high','medium','low','informational')",
            name="ck_rule_severity",
        ),
        sa.CheckConstraint(
            "rule_category IN ('duplicate_discount','medicaid_overlap',"
            "'contract_pharmacy_eligibility','split_billing','carve_in_out',"
            "'entity_eligibility','data_quality')",
            name="ck_rule_category",
        ),
        schema="audit",
    )

    # ------------------------------------------------------------------
    # 14. audit.investigation_cases
    # ------------------------------------------------------------------
    op.create_table(
        "investigation_cases",
        sa.Column("case_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("case_number", sa.String(50), nullable=False, unique=True),
        sa.Column("covered_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ref.covered_entities.ce_id"), nullable=False),
        sa.Column("case_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("assigned_to", sa.String(255)),
        sa.Column("escalated_to", sa.String(255)),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("due_date", sa.Date),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("financial_exposure_estimate", sa.Numeric(15, 2)),
        sa.Column("financial_exposure_confirmed", sa.Numeric(15, 2)),
        sa.Column("finding_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("agent_workflow_id", sa.String(255)),
        sa.Column("workflow_state", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("workflow_checkpoint", sa.Text),
        sa.Column("last_agent_activity_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('open','in_progress','pending_review','escalated',"
            "'closed','dismissed','on_hold')",
            name="ck_case_status",
        ),
        sa.CheckConstraint(
            "priority IN ('critical','high','medium','low')",
            name="ck_case_priority",
        ),
        sa.CheckConstraint(
            "case_type IN ('routine_audit','targeted_investigation','self_disclosure',"
            "'regulatory_inquiry','data_quality')",
            name="ck_case_type",
        ),
        schema="audit",
    )

    # ------------------------------------------------------------------
    # 15. audit.audit_findings
    # ------------------------------------------------------------------
    op.create_table(
        "audit_findings",
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("finding_code", sa.String(50), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("audit.compliance_rules.rule_id"), nullable=False),
        sa.Column("rule_code", sa.String(50), nullable=False),
        sa.Column("rule_version", sa.String(20), nullable=False),
        sa.Column("covered_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ref.covered_entities.ce_id"), nullable=False),
        sa.Column("investigation_case_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("audit.investigation_cases.case_id")),
        sa.Column("finding_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("detection_method", sa.String(30), nullable=False,
                  server_default="rules_engine"),
        sa.Column("confidence_score", sa.Numeric(5, 4)),
        sa.Column("financial_exposure", sa.Numeric(15, 2)),
        sa.Column("financial_exposure_methodology", sa.Text),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True)),
        sa.Column("purchase_date", sa.Date),
        sa.Column("dispense_id", postgresql.UUID(as_uuid=True)),
        sa.Column("dispense_date", sa.Date),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True)),
        sa.Column("claim_service_date", sa.Date),
        sa.Column("split_billing_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ops.split_billing.split_billing_id")),
        sa.Column("evidence_payload", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("entity_references", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("violation_period_start", sa.Date),
        sa.Column("violation_period_end", sa.Date),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", sa.String(255)),
        sa.Column("resolution_type", sa.String(30)),
        sa.Column("resolution_notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "severity IN ('critical','high','medium','low','informational')",
            name="ck_finding_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open','under_review','confirmed','dismissed',"
            "'remediated','appealed','escalated')",
            name="ck_finding_status",
        ),
        sa.CheckConstraint(
            "detection_method IN ('rules_engine','manual','ai_flagged','imported')",
            name="ck_finding_detection_method",
        ),
        sa.CheckConstraint(
            "resolution_type IS NULL OR resolution_type IN ("
            "'confirmed_violation','false_positive','remediated','appealed','insufficient_evidence')",
            name="ck_finding_resolution_type",
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_confidence_score",
        ),
        schema="audit",
    )

    # ------------------------------------------------------------------
    # 16. audit.reasoning_traces
    # ------------------------------------------------------------------
    op.create_table(
        "reasoning_traces",
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("investigation_case_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("audit.investigation_cases.case_id")),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("audit.audit_findings.finding_id")),
        sa.Column("parent_trace_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("audit.reasoning_traces.trace_id")),
        sa.Column("agent_id", sa.String(100)),
        sa.Column("agent_type", sa.String(50)),
        sa.Column("workflow_node", sa.String(100)),
        sa.Column("workflow_step_sequence", sa.Integer),
        sa.Column("model_id", sa.String(100)),
        sa.Column("prompt_template_id", sa.String(100)),
        sa.Column("prompt_template_version", sa.String(20)),
        sa.Column("input_context", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("reasoning_output", sa.Text),
        sa.Column("structured_output", postgresql.JSONB),
        sa.Column("citations", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence_score", sa.Numeric(5, 4)),
        sa.Column("human_review_required", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("human_review_requested_at", sa.DateTime(timezone=True)),
        sa.Column("human_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("human_reviewer", sa.String(255)),
        sa.Column("human_review_notes", sa.Text),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("cache_read_tokens", sa.Integer),
        sa.Column("cache_write_tokens", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "agent_type IS NULL OR agent_type IN ("
            "'investigator','summarizer','prioritizer','validator',"
            "'extractor','classifier','reporter','orchestrator')",
            name="ck_trace_agent_type",
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_trace_confidence",
        ),
        schema="audit",
    )

    # ------------------------------------------------------------------
    # 17. Indexes (ref schema)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE UNIQUE INDEX ux_ce_hrsa_id_current
            ON ref.covered_entities (hrsa_id) WHERE is_current = TRUE
    """)
    op.create_index("ix_ce_hrsa_id", "covered_entities", ["hrsa_id"], schema="ref")
    op.create_index("ix_ce_npi", "covered_entities", ["npi"], schema="ref",
                    postgresql_where=sa.text("npi IS NOT NULL"))
    op.execute(
        "CREATE INDEX ix_ce_name_trgm ON ref.covered_entities "
        "USING gin (entity_name gin_trgm_ops)"
    )

    op.execute("""
        CREATE UNIQUE INDEX ux_cp_npi_hrsa_current
            ON ref.contract_pharmacies (pharmacy_npi, hrsa_id)
            WHERE is_current = TRUE AND pharmacy_npi IS NOT NULL
    """)
    op.create_index("ix_cp_ce_id", "contract_pharmacies", ["covered_entity_id"], schema="ref")
    op.create_index("ix_cp_hrsa_id", "contract_pharmacies", ["hrsa_id"], schema="ref")

    op.create_index("ix_me_ce_period", "medicaid_exclusions",
                    ["covered_entity_id", "period_start", "period_end"], schema="ref")
    op.create_index("ix_me_hrsa_id", "medicaid_exclusions", ["hrsa_id"], schema="ref")

    op.execute("""
        CREATE UNIQUE INDEX ux_providers_npi_current
            ON ref.providers (npi) WHERE is_current = TRUE
    """)
    op.create_index("ix_providers_npi", "providers", ["npi"], schema="ref")
    op.create_index("ix_pt_provider_id", "provider_taxonomies", ["provider_id"], schema="ref")

    # ------------------------------------------------------------------
    # 18. Indexes (ops schema)
    # ------------------------------------------------------------------
    op.create_index("ix_purchases_ce_date", "purchases",
                    ["covered_entity_id", "purchase_date"], schema="ops")
    op.create_index("ix_purchases_ndc", "purchases", ["ndc_11", "purchase_date"], schema="ops")

    op.create_index("ix_dispenses_ce_date", "dispenses",
                    ["covered_entity_id", "dispense_date"], schema="ops")
    op.create_index("ix_dispenses_patient_date", "dispenses",
                    ["patient_id_hash", "dispense_date"], schema="ops")
    op.create_index("ix_dispenses_ndc", "dispenses", ["ndc_11", "dispense_date"], schema="ops")

    op.create_index("ix_claims_ce_date", "claims", ["covered_entity_id", "service_date"],
                    schema="ops")
    op.create_index("ix_claims_ndc", "claims", ["ndc_11", "service_date"], schema="ops")
    op.execute("""
        CREATE INDEX ix_claims_medicaid ON ops.claims (covered_entity_id, service_date)
        WHERE is_medicaid = TRUE
    """)

    op.create_index("ix_sb_ce_date", "split_billing", ["covered_entity_id", "service_date"],
                    schema="ops")
    op.execute("""
        CREATE INDEX ix_sb_dup_discount_risk ON ops.split_billing (covered_entity_id, service_date)
        WHERE duplicate_discount_risk = TRUE
    """)
    op.execute("""
        CREATE INDEX ix_sb_any_risk ON ops.split_billing (covered_entity_id, risk_score DESC)
        WHERE risk_score > 0
    """)

    # ------------------------------------------------------------------
    # 19. Indexes (audit schema)
    # ------------------------------------------------------------------
    op.create_index("ix_findings_ce_status", "audit_findings",
                    ["covered_entity_id", "status"], schema="audit")
    op.create_index("ix_findings_case", "audit_findings",
                    ["investigation_case_id"], schema="audit",
                    postgresql_where=sa.text("investigation_case_id IS NOT NULL"))
    op.create_index("ix_findings_type_severity", "audit_findings",
                    ["finding_type", "severity", "status"], schema="audit")
    op.execute("""
        CREATE INDEX ix_findings_evidence_gin ON audit.audit_findings
        USING gin (evidence_payload)
    """)

    op.create_index("ix_cases_ce_id", "investigation_cases",
                    ["covered_entity_id"], schema="audit")
    op.execute("""
        CREATE INDEX ix_cases_status_priority ON audit.investigation_cases (status, priority)
        WHERE status NOT IN ('closed', 'dismissed')
    """)

    op.create_index("ix_traces_session", "reasoning_traces",
                    ["session_id", "workflow_step_sequence"], schema="audit")
    op.create_index("ix_traces_case", "reasoning_traces",
                    ["investigation_case_id", "created_at"], schema="audit",
                    postgresql_where=sa.text("investigation_case_id IS NOT NULL"))


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("reasoning_traces", schema="audit")
    op.drop_table("audit_findings", schema="audit")
    op.drop_table("investigation_cases", schema="audit")
    op.drop_table("compliance_rules", schema="audit")
    op.drop_table("split_billing", schema="ops")
    op.execute("DROP TABLE IF EXISTS ops.claims_default")
    op.execute("DROP TABLE IF EXISTS ops.claims")
    op.execute("DROP TABLE IF EXISTS ops.dispenses_default")
    op.execute("DROP TABLE IF EXISTS ops.dispenses")
    op.execute("DROP TABLE IF EXISTS ops.purchases_default")
    op.execute("DROP TABLE IF EXISTS ops.purchases")
    op.drop_table("ndc_drugs", schema="ref")
    op.drop_table("provider_taxonomies", schema="ref")
    op.drop_table("providers", schema="ref")
    op.drop_table("medicaid_exclusions", schema="ref")
    op.drop_table("contract_pharmacies", schema="ref")
    op.drop_table("covered_entities", schema="ref")
    op.drop_table("ingestion_batches", schema="meta")
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE")
    op.execute("DROP SCHEMA IF EXISTS ops CASCADE")
    op.execute("DROP SCHEMA IF EXISTS ref CASCADE")
    op.execute("DROP SCHEMA IF EXISTS meta CASCADE")
