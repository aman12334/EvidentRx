"""
Phase 13 — Regulatory Intelligence & Policy Automation Layer tables.

Revision:      0008
Down revision: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision      = "0008"
down_revision = "0007"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── Schema ────────────────────────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS regulatory")

    # ── Policy domains ────────────────────────────────────────────────────────
    op.create_table(
        "policy_domains",
        sa.Column("domain",       sa.Text,    primary_key=True),
        sa.Column("label",        sa.Text,    nullable=False),
        sa.Column("description",  sa.Text),
        sa.Column("required",     sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at",   sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        schema="regulatory",
    )

    # ── Document families ─────────────────────────────────────────────────────
    op.create_table(
        "regulatory_document_families",
        sa.Column("family_id",       sa.Text,    primary_key=True),
        sa.Column("canonical_title", sa.Text,    nullable=False),
        sa.Column("source",          sa.Text,    nullable=False),
        sa.Column("domains",         postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("metadata",        postgresql.JSONB, nullable=False, server_default="{}"),
        schema="regulatory",
    )
    op.create_index(
        "idx_reg_families_source", "regulatory_document_families", ["source"],
        schema="regulatory",
    )

    # ── Regulatory documents ──────────────────────────────────────────────────
    op.create_table(
        "regulatory_documents",
        sa.Column("doc_id",           sa.Text,    primary_key=True),
        sa.Column("family_id",        sa.Text,    sa.ForeignKey("regulatory.regulatory_document_families.family_id"), nullable=False),
        sa.Column("tenant_id",        sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="SET NULL")),
        sa.Column("title",            sa.Text,    nullable=False),
        sa.Column("version",          sa.Text,    nullable=False),
        sa.Column("source",           sa.Text,    nullable=False),
        sa.Column("format",           sa.Text,    nullable=False),
        sa.Column("status",           sa.Text,    nullable=False, server_default="pending"),
        sa.Column("domains",          postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("content_hash",     sa.CHAR(64), nullable=False),
        sa.Column("raw_text",         sa.Text),
        sa.Column("summary",          sa.Text),
        sa.Column("word_count",       sa.Integer, nullable=False, server_default="0"),
        sa.Column("language",         sa.Text,    nullable=False, server_default="en"),
        sa.Column("issuing_body",     sa.Text),
        sa.Column("source_url",       sa.Text),
        sa.Column("effective_date",   sa.Text),
        sa.Column("expiry_date",      sa.Text),
        sa.Column("publication_date", sa.Text),
        sa.Column("ingested_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("indexed_at",       sa.TIMESTAMP(timezone=True)),
        sa.Column("last_checked_at",  sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("family_id", "version", name="uq_reg_doc_family_version"),
        sa.UniqueConstraint("content_hash", name="uq_reg_doc_content_hash"),
        schema="regulatory",
    )
    op.create_index("idx_reg_docs_family",   "regulatory_documents", ["family_id"],  schema="regulatory")
    op.create_index("idx_reg_docs_status",   "regulatory_documents", ["status"],     schema="regulatory")
    op.create_index("idx_reg_docs_ingested", "regulatory_documents", ["ingested_at"],schema="regulatory")

    # ── Ingestion records ─────────────────────────────────────────────────────
    op.create_table(
        "ingestion_records",
        sa.Column("record_id",         postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("doc_id",            sa.Text,    sa.ForeignKey("regulatory.regulatory_documents.doc_id"), nullable=False),
        sa.Column("tenant_id",         sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="SET NULL")),
        sa.Column("source_url",        sa.Text),
        sa.Column("triggered_by",      sa.Text,    nullable=False, server_default="system"),
        sa.Column("stages_completed",  postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("success",           sa.Boolean, nullable=False),
        sa.Column("error_message",     sa.Text),
        sa.Column("duration_ms",       sa.Float),
        sa.Column("created_at",        sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        schema="regulatory",
    )
    op.create_index("idx_reg_ingestion_doc",    "ingestion_records", ["doc_id"],    schema="regulatory")
    op.create_index("idx_reg_ingestion_tenant", "ingestion_records", ["tenant_id"], schema="regulatory",
                    postgresql_where=sa.text("tenant_id IS NOT NULL"))

    # ── Policy sync sources ───────────────────────────────────────────────────
    op.create_table(
        "policy_sync_sources",
        sa.Column("source_id",             postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name",                  sa.Text,    nullable=False),
        sa.Column("source_type",           sa.Text,    nullable=False),
        sa.Column("base_url",              sa.Text,    nullable=False),
        sa.Column("frequency",             sa.Text,    nullable=False, server_default="monthly"),
        sa.Column("domains",               postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("enabled",               sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_synced_at",        sa.TIMESTAMP(timezone=True)),
        sa.Column("next_sync_at",          sa.TIMESTAMP(timezone=True)),
        sa.Column("consecutive_failures",  sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at",            sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("metadata",              postgresql.JSONB, nullable=False, server_default="{}"),
        schema="regulatory",
    )
    op.create_index("idx_reg_sync_sources_next", "policy_sync_sources", ["next_sync_at"],
                    schema="regulatory",
                    postgresql_where=sa.text("enabled = TRUE"))

    # ── Policy diffs ──────────────────────────────────────────────────────────
    op.create_table(
        "policy_diffs",
        sa.Column("diff_id",           sa.Text,    primary_key=True),
        sa.Column("family_id",         sa.Text,    sa.ForeignKey("regulatory.regulatory_document_families.family_id"), nullable=False),
        sa.Column("prior_doc_id",      sa.Text,    sa.ForeignKey("regulatory.regulatory_documents.doc_id"), nullable=False),
        sa.Column("new_doc_id",        sa.Text,    sa.ForeignKey("regulatory.regulatory_documents.doc_id"), nullable=False),
        sa.Column("prior_version",     sa.Text,    nullable=False),
        sa.Column("new_version",       sa.Text,    nullable=False),
        sa.Column("overall_severity",  sa.Text,    nullable=False),
        sa.Column("change_count",      sa.Integer, nullable=False, server_default="0"),
        sa.Column("jaccard_similarity",sa.Float,   nullable=False, server_default="0.0"),
        sa.Column("summary",           sa.Text),
        sa.Column("content_hash",      sa.CHAR(64), nullable=False),
        sa.Column("diffed_at",         sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("tenant_id",         sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="SET NULL")),
        sa.UniqueConstraint("prior_doc_id", "new_doc_id", name="uq_reg_diff_pair"),
        schema="regulatory",
    )
    op.create_index("idx_reg_diffs_family",   "policy_diffs", ["family_id"],        schema="regulatory")
    op.create_index("idx_reg_diffs_severity", "policy_diffs", ["overall_severity"], schema="regulatory")
    op.create_index("idx_reg_diffs_diffed_at","policy_diffs", ["diffed_at"],        schema="regulatory")

    # ── Policy changes ────────────────────────────────────────────────────────
    op.create_table(
        "policy_changes",
        sa.Column("change_id",         postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("diff_id",           sa.Text,    sa.ForeignKey("regulatory.policy_diffs.diff_id"), nullable=False),
        sa.Column("category",          sa.Text,    nullable=False),
        sa.Column("severity",          sa.Text,    nullable=False),
        sa.Column("section",           sa.Text,    nullable=False),
        sa.Column("description",       sa.Text,    nullable=False),
        sa.Column("prior_text",        sa.Text),
        sa.Column("new_text",          sa.Text),
        sa.Column("operational_areas", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("keywords",          postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("change_index",      sa.Integer, nullable=False, server_default="0"),
        schema="regulatory",
    )
    op.create_index("idx_reg_changes_diff",    "policy_changes", ["diff_id"],  schema="regulatory")
    op.create_index("idx_reg_changes_severity","policy_changes", ["severity"], schema="regulatory")

    # ── Drift reports ─────────────────────────────────────────────────────────
    op.create_table(
        "drift_reports",
        sa.Column("report_id",        postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",        sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("detected_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("overall_severity", sa.Text,    nullable=False),
        sa.Column("finding_count",    sa.Integer, nullable=False, server_default="0"),
        sa.Column("docs_checked",     sa.Integer, nullable=False, server_default="0"),
        sa.Column("domains_checked",  postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("summary",          sa.Text,    nullable=False),
        schema="regulatory",
    )
    op.create_index("idx_reg_drift_tenant",   "drift_reports", ["tenant_id", "detected_at"], schema="regulatory")
    op.create_index("idx_reg_drift_severity", "drift_reports", ["overall_severity"],          schema="regulatory")

    # ── Drift findings ────────────────────────────────────────────────────────
    op.create_table(
        "drift_findings",
        sa.Column("finding_id",          postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_id",           postgresql.UUID, sa.ForeignKey("regulatory.drift_reports.report_id", ondelete="CASCADE"), nullable=False),
        sa.Column("drift_type",          sa.Text,    nullable=False),
        sa.Column("severity",            sa.Text,    nullable=False),
        sa.Column("title",               sa.Text,    nullable=False),
        sa.Column("description",         sa.Text,    nullable=False),
        sa.Column("affected_docs",       postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("affected_rules",      postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("affected_workflows",  postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("diff_id",             sa.Text,    sa.ForeignKey("regulatory.policy_diffs.diff_id")),
        sa.Column("evidence",            postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("recommendation",      sa.Text),
        sa.Column("detected_at",         sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        schema="regulatory",
    )
    op.create_index("idx_reg_findings_report",  "drift_findings", ["report_id"],  schema="regulatory")
    op.create_index("idx_reg_findings_severity","drift_findings", ["severity"],   schema="regulatory")
    op.create_index("idx_reg_findings_type",    "drift_findings", ["drift_type"], schema="regulatory")

    # ── Impact reports ────────────────────────────────────────────────────────
    op.create_table(
        "impact_reports",
        sa.Column("report_id",           sa.Text,    primary_key=True),
        sa.Column("tenant_id",           sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type",         sa.Text,    nullable=False),
        sa.Column("source_id",           sa.Text,    nullable=False),
        sa.Column("severity",            sa.Text,    nullable=False),
        sa.Column("affected_domain_count",sa.Integer,nullable=False, server_default="0"),
        sa.Column("workflow_count",      sa.Integer, nullable=False, server_default="0"),
        sa.Column("rule_count",          sa.Integer, nullable=False, server_default="0"),
        sa.Column("entity_count",        sa.Integer, nullable=False, server_default="0"),
        sa.Column("narrative",           sa.Text),
        sa.Column("action_required_by",  sa.Text),
        sa.Column("fin_risk_low_usd",    sa.BigInteger),
        sa.Column("fin_risk_high_usd",   sa.BigInteger),
        sa.Column("fin_risk_basis",      sa.Text),
        sa.Column("fin_risk_confidence", sa.Float),
        sa.Column("analyzed_at",         sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        schema="regulatory",
    )
    op.create_index("idx_reg_impact_tenant", "impact_reports", ["tenant_id"],              schema="regulatory")
    op.create_index("idx_reg_impact_source", "impact_reports", ["source_type", "source_id"],schema="regulatory")

    # ── Affected elements ─────────────────────────────────────────────────────
    op.create_table(
        "affected_elements",
        sa.Column("element_id",           postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_id",            sa.Text, sa.ForeignKey("regulatory.impact_reports.report_id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id",          sa.Text, nullable=False),
        sa.Column("element_type",         sa.Text, nullable=False),
        sa.Column("name",                 sa.Text),
        sa.Column("severity",             sa.Text, nullable=False),
        sa.Column("remediation_required", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("notes",                sa.Text),
        schema="regulatory",
    )
    op.create_index("idx_reg_affected_report","affected_elements", ["report_id"],    schema="regulatory")
    op.create_index("idx_reg_affected_type",  "affected_elements", ["element_type"], schema="regulatory")

    # ── Policy recommendations ────────────────────────────────────────────────
    op.create_table(
        "policy_recommendations",
        sa.Column("rec_id",           sa.Text,    primary_key=True),
        sa.Column("tenant_id",        sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("rec_type",         sa.Text,    nullable=False),
        sa.Column("title",            sa.Text,    nullable=False),
        sa.Column("rationale",        sa.Text,    nullable=False),
        sa.Column("proposed_change",  sa.Text,    nullable=False),
        sa.Column("affected_elements",postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("source_type",      sa.Text,    nullable=False),
        sa.Column("source_id",        sa.Text,    nullable=False),
        sa.Column("status",           sa.Text,    nullable=False, server_default="draft"),
        sa.Column("priority",         sa.Text,    nullable=False, server_default="normal"),
        sa.Column("content_hash",     sa.CHAR(64),nullable=False),
        sa.Column("version",          sa.Integer, nullable=False, server_default="1"),
        sa.Column("prior_rec_id",     sa.Text,    sa.ForeignKey("regulatory.policy_recommendations.rec_id")),
        sa.Column("created_by",       sa.Text,    nullable=False, server_default="system"),
        sa.Column("created_at",       sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("submitted_at",     sa.TIMESTAMP(timezone=True)),
        sa.Column("decided_at",       sa.TIMESTAMP(timezone=True)),
        sa.Column("decided_by",       sa.Text),
        sa.Column("decision_notes",   sa.Text),
        sa.Column("implemented_at",   sa.TIMESTAMP(timezone=True)),
        sa.Column("action_by_date",   sa.Text),
        sa.Column("metadata",         postgresql.JSONB, nullable=False, server_default="{}"),
        schema="regulatory",
    )
    op.create_index("idx_reg_recs_tenant_status","policy_recommendations", ["tenant_id", "status"], schema="regulatory")
    op.create_index("idx_reg_recs_priority",     "policy_recommendations", ["priority"],            schema="regulatory")
    op.create_index("idx_reg_recs_source",       "policy_recommendations", ["source_type","source_id"], schema="regulatory")
    op.create_index("idx_reg_recs_created",      "policy_recommendations", ["created_at"],          schema="regulatory")

    # ── Recommendation lineage ────────────────────────────────────────────────
    op.create_table(
        "recommendation_lineage",
        sa.Column("entry_id",     postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rec_id",       sa.Text,    sa.ForeignKey("regulatory.policy_recommendations.rec_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event",        sa.Text,    nullable=False),
        sa.Column("actor_id",     sa.Text,    nullable=False),
        sa.Column("notes",        sa.Text),
        sa.Column("content_hash", sa.CHAR(64),nullable=False),
        sa.Column("occurred_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        schema="regulatory",
    )
    op.create_index("idx_reg_lineage_rec","recommendation_lineage", ["rec_id", "occurred_at"], schema="regulatory")

    # ── Policy citations ──────────────────────────────────────────────────────
    op.create_table(
        "policy_citations",
        sa.Column("citation_id",     postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("investigation_id",sa.Text,    nullable=False),
        sa.Column("doc_id",          sa.Text,    sa.ForeignKey("regulatory.regulatory_documents.doc_id"), nullable=False),
        sa.Column("doc_version",     sa.Text,    nullable=False),
        sa.Column("doc_title",       sa.Text,    nullable=False),
        sa.Column("section",         sa.Text,    nullable=False),
        sa.Column("excerpt",         sa.Text),
        sa.Column("rationale",       sa.Text),
        sa.Column("strength",        sa.Text,    nullable=False),
        sa.Column("domain",          sa.Text),
        sa.Column("asserted_by",     sa.Text,    nullable=False, server_default="system"),
        sa.Column("asserted_at",     sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("effective_at",    sa.Text),
        sa.Column("confidence",      sa.Float,   nullable=False, server_default="1.0"),
        sa.Column("human_verified",  sa.Boolean, nullable=False, server_default="false"),
        schema="regulatory",
    )
    op.create_index("idx_reg_citations_investigation","policy_citations", ["investigation_id"], schema="regulatory")
    op.create_index("idx_reg_citations_doc",          "policy_citations", ["doc_id"],           schema="regulatory")
    op.create_index("idx_reg_citations_strength",     "policy_citations", ["strength"],         schema="regulatory")

    # ── Investigation policy contexts ─────────────────────────────────────────
    op.create_table(
        "investigation_policy_contexts",
        sa.Column("context_id",          postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("investigation_id",    sa.Text,    nullable=False),
        sa.Column("tenant_id",           sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_as_of",       sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("applicable_domains",  postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("escalation_policy_notes", sa.Text),
        sa.Column("compliance_rationale",sa.Text),
        sa.Column("created_at",          sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("created_by",          sa.Text,    nullable=False, server_default="system"),
        sa.Column("metadata",            postgresql.JSONB, nullable=False, server_default="{}"),
        schema="regulatory",
    )
    op.create_index("idx_reg_inv_contexts_investigation","investigation_policy_contexts", ["investigation_id"], schema="regulatory")
    op.create_index("idx_reg_inv_contexts_tenant",       "investigation_policy_contexts", ["tenant_id"],        schema="regulatory")

    # ── Readiness snapshots ───────────────────────────────────────────────────
    op.create_table(
        "readiness_snapshots",
        sa.Column("snapshot_id",     postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",       sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessed_at",     sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("score",           sa.Float,   nullable=False),
        sa.Column("band",            sa.Text,    nullable=False),
        sa.Column("total_deduction", sa.Float,   nullable=False, server_default="0.0"),
        sa.Column("domains_covered", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("domains_missing", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("docs_evaluated",  sa.Integer, nullable=False, server_default="0"),
        sa.Column("drift_findings",  sa.Integer, nullable=False, server_default="0"),
        sa.Column("pending_recs",    sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary",         sa.Text),
        sa.Column("generated_by",    sa.Text,    nullable=False, server_default="system"),
        schema="regulatory",
    )
    op.create_index("idx_reg_readiness_tenant","readiness_snapshots", ["tenant_id", "assessed_at"], schema="regulatory")
    op.create_index("idx_reg_readiness_band",  "readiness_snapshots", ["band"],                     schema="regulatory")

    # ── Readiness signals ─────────────────────────────────────────────────────
    op.create_table(
        "readiness_signals",
        sa.Column("signal_id",    postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("snapshot_id",  postgresql.UUID, sa.ForeignKey("regulatory.readiness_snapshots.snapshot_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",         sa.Text,    nullable=False),
        sa.Column("category",     sa.Text,    nullable=False),
        sa.Column("deduction",    sa.Float,   nullable=False),
        sa.Column("reason",       sa.Text,    nullable=False),
        sa.Column("severity",     sa.Text,    nullable=False),
        sa.Column("affected_ids", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        schema="regulatory",
    )
    op.create_index("idx_reg_signals_snapshot","readiness_signals", ["snapshot_id"], schema="regulatory")
    op.create_index("idx_reg_signals_severity","readiness_signals", ["severity"],    schema="regulatory")

    # ── Timeline events ───────────────────────────────────────────────────────
    op.create_table(
        "timeline_events",
        sa.Column("event_id",     postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",    sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type",   sa.Text,    nullable=False),
        sa.Column("occurred_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("title",        sa.Text,    nullable=False),
        sa.Column("description",  sa.Text),
        sa.Column("external_id",  sa.Text,    nullable=False),
        sa.Column("external_type",sa.Text,    nullable=False),
        sa.Column("severity",     sa.Text,    nullable=False, server_default="informational"),
        sa.Column("actor_id",     sa.Text,    nullable=False, server_default="system"),
        sa.Column("domain",       sa.Text),
        sa.Column("tags",         postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("metadata",     postgresql.JSONB, nullable=False, server_default="{}"),
        schema="regulatory",
    )
    op.create_index("idx_reg_timeline_tenant",  "timeline_events", ["tenant_id", "occurred_at"], schema="regulatory")
    op.create_index("idx_reg_timeline_external","timeline_events", ["external_id", "external_type"], schema="regulatory")
    op.create_index("idx_reg_timeline_type",    "timeline_events", ["event_type"],               schema="regulatory")

    # ── Activation workflows ──────────────────────────────────────────────────
    op.create_table(
        "activation_workflows",
        sa.Column("workflow_id",         postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",           sa.Text(), sa.ForeignKey("saas.tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_id",              sa.Text,    sa.ForeignKey("regulatory.regulatory_documents.doc_id"), nullable=False),
        sa.Column("doc_version",         sa.Text,    nullable=False),
        sa.Column("doc_title",           sa.Text,    nullable=False),
        sa.Column("status",              sa.Text,    nullable=False, server_default="pending_review"),
        sa.Column("priority",            sa.Text,    nullable=False, server_default="normal"),
        sa.Column("created_by",          sa.Text,    nullable=False),
        sa.Column("created_at",          sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("reviewer_id",         sa.Text),
        sa.Column("review_started_at",   sa.TIMESTAMP(timezone=True)),
        sa.Column("approver_id",         sa.Text),
        sa.Column("approved_at",         sa.TIMESTAMP(timezone=True)),
        sa.Column("approval_notes",      sa.Text),
        sa.Column("activator_id",        sa.Text),
        sa.Column("activated_at",        sa.TIMESTAMP(timezone=True)),
        sa.Column("rejected_by",         sa.Text),
        sa.Column("rejected_at",         sa.TIMESTAMP(timezone=True)),
        sa.Column("rejection_reason",    sa.Text),
        sa.Column("action_required_by",  sa.Text),
        sa.Column("metadata",            postgresql.JSONB, nullable=False, server_default="{}"),
        schema="regulatory",
    )
    op.create_index("idx_reg_workflows_tenant_status","activation_workflows", ["tenant_id","status"], schema="regulatory")
    op.create_index("idx_reg_workflows_doc",          "activation_workflows", ["doc_id"],             schema="regulatory")

    # ── Workflow audit entries ────────────────────────────────────────────────
    op.create_table(
        "workflow_audit_entries",
        sa.Column("entry_id",    postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workflow_id", postgresql.UUID, sa.ForeignKey("regulatory.activation_workflows.workflow_id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status",   sa.Text,    nullable=False),
        sa.Column("actor_id",    sa.Text,    nullable=False),
        sa.Column("action",      sa.Text,    nullable=False),
        sa.Column("notes",       sa.Text),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        schema="regulatory",
    )
    op.create_index("idx_reg_wf_audit_workflow","workflow_audit_entries", ["workflow_id","occurred_at"], schema="regulatory")

    # ── Graph nodes ───────────────────────────────────────────────────────────
    op.create_table(
        "graph_nodes",
        sa.Column("node_id",     sa.Text,    primary_key=True),
        sa.Column("node_type",   sa.Text,    nullable=False),
        sa.Column("label",       sa.Text,    nullable=False),
        sa.Column("external_id", sa.Text,    nullable=False),
        sa.Column("domain",      sa.Text),
        sa.Column("valid_from",  sa.TIMESTAMP(timezone=True)),
        sa.Column("valid_until", sa.TIMESTAMP(timezone=True)),
        sa.Column("properties",  postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        schema="regulatory",
    )
    op.create_index("idx_reg_nodes_type",    "graph_nodes", ["node_type"],   schema="regulatory")
    op.create_index("idx_reg_nodes_external","graph_nodes", ["external_id"], schema="regulatory")

    # ── Graph edges ───────────────────────────────────────────────────────────
    op.create_table(
        "graph_edges",
        sa.Column("edge_id",      sa.Text,    primary_key=True),
        sa.Column("source_id",    sa.Text,    sa.ForeignKey("regulatory.graph_nodes.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id",    sa.Text,    sa.ForeignKey("regulatory.graph_nodes.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship", sa.Text,    nullable=False),
        sa.Column("confidence",   sa.Float,   nullable=False, server_default="1.0"),
        sa.Column("valid_from",   sa.TIMESTAMP(timezone=True)),
        sa.Column("valid_until",  sa.TIMESTAMP(timezone=True)),
        sa.Column("properties",   postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at",   sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("created_by",   sa.Text,    nullable=False, server_default="system"),
        schema="regulatory",
    )
    op.create_index("idx_reg_edges_source",      "graph_edges", ["source_id"],             schema="regulatory")
    op.create_index("idx_reg_edges_target",      "graph_edges", ["target_id"],             schema="regulatory")
    op.create_index("idx_reg_edges_relationship","graph_edges", ["relationship"],          schema="regulatory")

    # ── Evaluation scenarios ──────────────────────────────────────────────────
    op.create_table(
        "evaluation_scenarios",
        sa.Column("scenario_id",     postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name",            sa.Text,    nullable=False),
        sa.Column("description",     sa.Text),
        sa.Column("tenant_id",       sa.Text(), nullable=False),
        sa.Column("stages",          postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("assertion_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("metadata",        postgresql.JSONB, nullable=False, server_default="{}"),
        schema="regulatory",
    )

    # ── Evaluation results ────────────────────────────────────────────────────
    op.create_table(
        "evaluation_results",
        sa.Column("run_id",          postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("scenario_id",     postgresql.UUID, sa.ForeignKey("regulatory.evaluation_scenarios.scenario_id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id",       sa.Text(), nullable=False),
        sa.Column("run_at",          sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("passed",          sa.Boolean, nullable=False),
        sa.Column("pass_count",      sa.Integer, nullable=False, server_default="0"),
        sa.Column("fail_count",      sa.Integer, nullable=False, server_default="0"),
        sa.Column("stages_executed", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("duration_ms",     sa.Float),
        sa.Column("error",           sa.Text),
        sa.Column("result_summary",  postgresql.JSONB, nullable=False, server_default="{}"),
        schema="regulatory",
    )
    op.create_index("idx_reg_eval_results_scenario","evaluation_results", ["scenario_id","run_at"], schema="regulatory")
    op.create_index("idx_reg_eval_results_passed",  "evaluation_results", ["passed","run_at"],      schema="regulatory")

    # ── Seed domains ──────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO regulatory.policy_domains (domain, label, description, required) VALUES
            ('drug_340b',           '340B Drug Pricing Program',         'HRSA 340B program eligibility, compliance, and audit requirements.',                TRUE),
            ('contract_pharmacy',   'Contract Pharmacy',                  'Contract pharmacy arrangement regulations and oversight requirements.',            TRUE),
            ('audit_requirements',  'Audit Requirements',                 'Federal and state audit, record-keeping, and examination requirements.',           TRUE),
            ('medicaid_exclusions', 'Medicaid Drug Rebate Exclusions',    'Medicaid duplicate discount prevention and state agency coordination.',           FALSE),
            ('manufacturer_access', 'Manufacturer Access Restrictions',   'Drug manufacturer obligations and restrictions under 340B program guidance.',      FALSE),
            ('covered_entity',      'Covered Entity Eligibility',         'HRSA covered entity eligibility, registration, and recertification requirements.',FALSE),
            ('hipaa_privacy',       'HIPAA Privacy & Security',           'PHI handling, breach notification, and security rule compliance.',                 FALSE),
            ('cms_billing',         'CMS Billing & Claims',               'CMS billing, claims submission, and reimbursement regulations.',                  FALSE),
            ('state_pharmacy_law',  'State Pharmacy Law',                 'State board of pharmacy licensing, dispensing, and operational requirements.',     FALSE),
            ('dea_controlled',      'DEA Controlled Substances',          'DEA controlled substance scheduling, dispensing, and record-keeping.',             FALSE)
        ON CONFLICT (domain) DO NOTHING
    """)


def downgrade() -> None:
    for table in [
        "evaluation_results",
        "evaluation_scenarios",
        "graph_edges",
        "graph_nodes",
        "workflow_audit_entries",
        "activation_workflows",
        "timeline_events",
        "readiness_signals",
        "readiness_snapshots",
        "investigation_policy_contexts",
        "policy_citations",
        "recommendation_lineage",
        "policy_recommendations",
        "affected_elements",
        "impact_reports",
        "drift_findings",
        "drift_reports",
        "policy_changes",
        "policy_diffs",
        "policy_sync_sources",
        "ingestion_records",
        "regulatory_documents",
        "regulatory_document_families",
        "policy_domains",
    ]:
        op.drop_table(table, schema="regulatory")

    op.execute("DROP SCHEMA IF EXISTS regulatory CASCADE")
