"""
Migration 0005: Interoperability Infrastructure Tables

Adds the interop schema and all Phase 10 tables:
  - interop.connector_configs
  - interop.ingestion_jobs
  - interop.source_lineage
  - interop.hl7_dead_letters
  - interop.sync_cursors

Revision: 0005
Previous: 0004 (auth_tables)
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision  = "0005"
down_revision = "0004"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── interop schema ────────────────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS interop")

    # ── connector_configs ─────────────────────────────────────────────────────
    op.create_table(
        "connector_configs",
        sa.Column("connector_id",   sa.Text(),              nullable=False),
        sa.Column("tenant_id",      postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type",    sa.Text(),              nullable=False),
        sa.Column("vendor",         sa.Text(),              nullable=False, server_default="generic"),
        sa.Column("display_name",   sa.Text(),              nullable=False),
        sa.Column("base_url",       sa.Text()),
        sa.Column("auth_type",      sa.Text(),              nullable=False, server_default="bearer"),
        sa.Column("timeout_sec",    sa.Integer(),           nullable=False, server_default="30"),
        sa.Column("max_retries",    sa.Integer(),           nullable=False, server_default="3"),
        sa.Column("page_size",      sa.Integer(),           nullable=False, server_default="200"),
        sa.Column("resource_types", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("secret_ref",     sa.Text()),
        sa.Column("extra",          postgresql.JSONB(),     nullable=False, server_default="{}"),
        sa.Column("is_active",      sa.Boolean(),           nullable=False, server_default="true"),
        sa.Column("created_at",     sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",     sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("connector_id", "tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["ref.covered_entities.ce_id"],
            ondelete="CASCADE",
        ),
        schema="interop",
    )
    op.create_index(
        "idx_connector_configs_tenant",
        "connector_configs",
        ["tenant_id"],
        schema="interop",
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.create_index(
        "idx_connector_configs_source_type",
        "connector_configs",
        ["source_type"],
        schema="interop",
    )

    # ── ingestion_jobs ────────────────────────────────────────────────────────
    op.create_table(
        "ingestion_jobs",
        sa.Column("job_id",          postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("connector_id",    sa.Text(),              nullable=False),
        sa.Column("tenant_id",       postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type",   sa.Text(),              nullable=False),
        sa.Column("source_system",   sa.Text(),              nullable=False),
        sa.Column("ingest_mode",     sa.Text(),              nullable=False, server_default="incremental"),
        sa.Column("status",          sa.Text(),              nullable=False, server_default="running"),
        sa.Column("started_at",      sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at",     sa.TIMESTAMP(timezone=True)),
        sa.Column("records_fetched", sa.Integer(),           nullable=False, server_default="0"),
        sa.Column("records_written", sa.Integer(),           nullable=False, server_default="0"),
        sa.Column("records_failed",  sa.Integer(),           nullable=False, server_default="0"),
        sa.Column("records_duplicate", sa.Integer(),         nullable=False, server_default="0"),
        sa.Column("cursor_start",    sa.Text()),
        sa.Column("cursor_end",      sa.Text()),
        sa.Column("error_summary",   sa.Text()),
        sa.PrimaryKeyConstraint("job_id"),
        schema="interop",
    )
    op.create_index(
        "idx_ingestion_jobs_connector",
        "ingestion_jobs",
        ["connector_id", "tenant_id", sa.text("started_at DESC")],
        schema="interop",
    )
    op.create_index(
        "idx_ingestion_jobs_tenant_status",
        "ingestion_jobs",
        ["tenant_id", "status", sa.text("started_at DESC")],
        schema="interop",
    )

    # ── source_lineage ────────────────────────────────────────────────────────
    op.create_table(
        "source_lineage",
        sa.Column("lineage_id",           postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",            postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system",        sa.Text(),              nullable=False),
        sa.Column("resource_type",        sa.Text(),              nullable=False),
        sa.Column("canonical_type",       sa.Text()),
        sa.Column("checksum",             sa.Text()),
        sa.Column("transformation_steps", postgresql.JSONB(),     nullable=False, server_default="[]"),
        sa.Column("raw_ref",              sa.Text()),
        sa.Column("canonical_ref",        sa.Text()),
        sa.Column("is_valid",             sa.Boolean(),           nullable=False, server_default="true"),
        sa.Column("error_summary",        sa.Text()),
        sa.Column("created_at",           sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("lineage_id"),
        schema="interop",
    )
    op.create_index(
        "idx_source_lineage_tenant",
        "source_lineage",
        ["tenant_id", sa.text("created_at DESC")],
        schema="interop",
    )
    op.create_index(
        "idx_source_lineage_checksum",
        "source_lineage",
        ["checksum"],
        schema="interop",
        postgresql_where=sa.text("checksum IS NOT NULL"),
    )

    # ── hl7_dead_letters ──────────────────────────────────────────────────────
    op.create_table(
        "hl7_dead_letters",
        sa.Column("dlq_id",           postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",        postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason",           sa.Text(),              nullable=False),
        sa.Column("raw_message",      sa.Text(),              nullable=False),
        sa.Column("message_type",     sa.Text(),              nullable=False, server_default="UNKNOWN"),
        sa.Column("trigger_event",    sa.Text(),              nullable=False, server_default=""),
        sa.Column("message_id",       sa.Text(),              nullable=False, server_default=""),
        sa.Column("sending_facility", sa.Text(),              nullable=False, server_default=""),
        sa.Column("parse_errors",     postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("detail",           sa.Text(),              nullable=False, server_default=""),
        sa.Column("tags",             postgresql.JSONB(),     nullable=False, server_default="{}"),
        sa.Column("replayed",         sa.Boolean(),           nullable=False, server_default="false"),
        sa.Column("replay_count",     sa.Integer(),           nullable=False, server_default="0"),
        sa.Column("enqueued_at",      sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("replayed_at",      sa.TIMESTAMP(timezone=True)),
        sa.PrimaryKeyConstraint("dlq_id"),
        schema="interop",
    )
    op.create_index(
        "idx_hl7_dlq_tenant",
        "hl7_dead_letters",
        ["tenant_id", sa.text("enqueued_at DESC")],
        schema="interop",
    )
    op.create_index(
        "idx_hl7_dlq_unprocessed",
        "hl7_dead_letters",
        ["tenant_id", "reason"],
        schema="interop",
        postgresql_where=sa.text("replayed = FALSE"),
    )

    # ── sync_cursors ──────────────────────────────────────────────────────────
    op.create_table(
        "sync_cursors",
        sa.Column("connector_id",   sa.Text(),              nullable=False),
        sa.Column("tenant_id",      postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type",  sa.Text(),              nullable=False),
        sa.Column("last_value",     sa.Text()),
        sa.Column("last_synced",    sa.TIMESTAMP(timezone=True)),
        sa.Column("records_total",  sa.BigInteger(),         nullable=False, server_default="0"),
        sa.Column("updated_at",     sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("connector_id", "tenant_id", "resource_type"),
        schema="interop",
    )

    # ── updated_at trigger for connector_configs ───────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION interop.touch_updated_at()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_connector_configs_updated_at
        BEFORE UPDATE ON interop.connector_configs
        FOR EACH ROW EXECUTE FUNCTION interop.touch_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_connector_configs_updated_at ON interop.connector_configs")
    op.execute("DROP FUNCTION IF EXISTS interop.touch_updated_at()")
    op.drop_table("sync_cursors",      schema="interop")
    op.drop_table("hl7_dead_letters",  schema="interop")
    op.drop_table("source_lineage",    schema="interop")
    op.drop_table("ingestion_jobs",    schema="interop")
    op.drop_table("connector_configs", schema="interop")
    op.execute("DROP SCHEMA IF EXISTS interop CASCADE")
