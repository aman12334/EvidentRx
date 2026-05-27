"""
Alembic migration: 0006 — Continuous Learning Layer

Creates all Phase 11 learning schema tables.

Revision: 0006
Down revision: 0005

Tables created
──────────────
  learning.feedback_records
  learning.feedback_lineage
  learning.calibration_snapshots
  learning.prompt_versions
  learning.workflow_versions
  learning.benchmark_suites
  learning.evaluation_runs
  learning.experiments
  learning.experiment_runs
  learning.approval_requests
  learning.approval_decisions
  learning.memory_entries
  learning.governance_audit
  learning.recommendation_records

Functions
─────────
  learning.touch_updated_at()
  learning.purge_expired_memory(text)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ── Revision identifiers ───────────────────────────────────────────────────────
revision       = "0006"
down_revision  = "0005"
branch_labels  = None
depends_on     = None


def upgrade() -> None:
    # ── Schema ─────────────────────────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS learning")

    # ── touch_updated_at trigger function ──────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION learning.touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # ── 1. feedback_records ────────────────────────────────────────────────────
    op.create_table(
        "feedback_records",
        sa.Column("feedback_id",   postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",     sa.Text(), nullable=False),
        sa.Column("analyst_id",    sa.Text(), nullable=False),
        sa.Column("feedback_type", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("artifact_id",   sa.Text(), nullable=False),
        sa.Column("status",        sa.Text(), nullable=False, server_default="pending"),
        sa.Column("lineage_hash",  sa.Text(), nullable=False),
        sa.Column("content",       postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at",    sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        schema="learning",
    )
    op.create_index("idx_feedback_tenant_type",
                    "feedback_records", ["tenant_id", "feedback_type"], schema="learning")
    op.create_index("idx_feedback_artifact",
                    "feedback_records", ["tenant_id", "artifact_id"], schema="learning")
    op.create_index("idx_feedback_analyst",
                    "feedback_records", ["tenant_id", "analyst_id"], schema="learning")
    op.create_index("idx_feedback_created_at",
                    "feedback_records", [sa.text("created_at DESC")], schema="learning")

    # ── 2. feedback_lineage ────────────────────────────────────────────────────
    op.create_table(
        "feedback_lineage",
        sa.Column("entry_id",     postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("feedback_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("learning.feedback_records.feedback_id"), nullable=False),
        sa.Column("tenant_id",    sa.Text(), nullable=False),
        sa.Column("event_type",   sa.Text(), nullable=False),
        sa.Column("prior_hash",   sa.Text(), nullable=True),
        sa.Column("chain_hash",   sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("actor",        sa.Text(), nullable=False),
        sa.Column("occurred_at",  sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("metadata",     postgresql.JSONB(), nullable=False, server_default="{}"),
        schema="learning",
    )
    op.create_index("idx_feedback_lineage_feedback",
                    "feedback_lineage", ["feedback_id"], schema="learning")
    op.create_index("idx_feedback_lineage_tenant",
                    "feedback_lineage", [sa.text("tenant_id, occurred_at DESC")],
                    schema="learning")

    # ── 3. calibration_snapshots ───────────────────────────────────────────────
    op.create_table(
        "calibration_snapshots",
        sa.Column("snapshot_id",        postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",          sa.Text(), nullable=False),
        sa.Column("version",            sa.Text(), nullable=False),
        sa.Column("status",             sa.Text(), nullable=False, server_default="draft"),
        sa.Column("rule_calibrations",  postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("thresholds",         postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("feedback_window_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("total_fp",           sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_fn",           sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_confirmed",    sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cleared",      sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash",       sa.Text(), nullable=False),
        sa.Column("created_at",         sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_by",         sa.Text(), nullable=False),
        sa.Column("approved_by",        sa.Text(), nullable=True),
        sa.Column("approved_at",        sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("activated_at",       sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("superseded_at",      sa.TIMESTAMP(timezone=True), nullable=True),
        schema="learning",
    )
    op.create_index("idx_calibration_tenant_status",
                    "calibration_snapshots", ["tenant_id", "status"], schema="learning")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_active
        ON learning.calibration_snapshots (tenant_id)
        WHERE status = 'active'
    """)

    # ── 4. prompt_versions ────────────────────────────────────────────────────
    op.create_table(
        "prompt_versions",
        sa.Column("prompt_id",         postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",         sa.Text(), nullable=False),
        sa.Column("prompt_name",       sa.Text(), nullable=False),
        sa.Column("version",           sa.Text(), nullable=False),
        sa.Column("title",             sa.Text(), nullable=False),
        sa.Column("template",          sa.Text(), nullable=False),
        sa.Column("system_context",    sa.Text(), nullable=False, server_default=""),
        sa.Column("model_target",      sa.Text(), nullable=False),
        sa.Column("status",            sa.Text(), nullable=False, server_default="draft"),
        sa.Column("content_hash",      sa.Text(), nullable=False),
        sa.Column("change_summary",    sa.Text(), nullable=False, server_default=""),
        sa.Column("test_coverage",     sa.Numeric(4, 3), nullable=False, server_default="0.0"),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at",        sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_by",        sa.Text(), nullable=False),
        sa.Column("approved_by",       sa.Text(), nullable=True),
        sa.Column("approved_at",       sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("metadata",          postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("tenant_id", "prompt_name", "version",
                            name="uq_prompt_versions_tenant_name_version"),
        schema="learning",
    )
    op.execute("""
        ALTER TABLE learning.prompt_versions
        ADD CONSTRAINT fk_prompt_parent FOREIGN KEY (parent_version_id)
        REFERENCES learning.prompt_versions(prompt_id)
    """)
    op.create_index("idx_prompt_tenant_name",
                    "prompt_versions", ["tenant_id", "prompt_name", "status"], schema="learning")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_prompt_active
        ON learning.prompt_versions (tenant_id, prompt_name)
        WHERE status = 'active'
    """)

    # ── 5. workflow_versions ──────────────────────────────────────────────────
    op.create_table(
        "workflow_versions",
        sa.Column("workflow_id",        postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",          sa.Text(), nullable=False),
        sa.Column("workflow_name",      sa.Text(), nullable=False),
        sa.Column("version",            sa.Text(), nullable=False),
        sa.Column("title",              sa.Text(), nullable=False),
        sa.Column("description",        sa.Text(), nullable=False, server_default=""),
        sa.Column("steps",              postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("output_contract",    postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status",             sa.Text(), nullable=False, server_default="draft"),
        sa.Column("content_hash",       sa.Text(), nullable=False),
        sa.Column("change_summary",     sa.Text(), nullable=False, server_default=""),
        sa.Column("min_agent_version",  sa.Text(), nullable=False, server_default="1.0.0"),
        sa.Column("parent_version_id",  postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at",         sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_by",         sa.Text(), nullable=False),
        sa.Column("approved_by",        sa.Text(), nullable=True),
        sa.Column("approved_at",        sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("metadata",           postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("tenant_id", "workflow_name", "version",
                            name="uq_workflow_versions_tenant_name_version"),
        schema="learning",
    )
    op.execute("""
        ALTER TABLE learning.workflow_versions
        ADD CONSTRAINT fk_workflow_parent FOREIGN KEY (parent_version_id)
        REFERENCES learning.workflow_versions(workflow_id)
    """)
    op.create_index("idx_workflow_tenant_name",
                    "workflow_versions", ["tenant_id", "workflow_name", "status"],
                    schema="learning")

    # ── 6. benchmark_suites ────────────────────────────────────────────────────
    op.create_table(
        "benchmark_suites",
        sa.Column("benchmark_id",    postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",       sa.Text(), nullable=False),
        sa.Column("name",            sa.Text(), nullable=False),
        sa.Column("version",         sa.Text(), nullable=False),
        sa.Column("description",     sa.Text(), nullable=False, server_default=""),
        sa.Column("status",          sa.Text(), nullable=False, server_default="draft"),
        sa.Column("case_count",      sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash",    sa.Text(), nullable=False, server_default=""),
        sa.Column("category_distribution", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_by",      sa.Text(), nullable=False),
        sa.Column("published_at",    sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("parent_version",  sa.Text(), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", "version",
                            name="uq_benchmark_suites_tenant_name_version"),
        schema="learning",
    )
    op.create_index("idx_benchmark_tenant",
                    "benchmark_suites", ["tenant_id", "status"], schema="learning")

    # ── 7. evaluation_runs ────────────────────────────────────────────────────
    op.create_table(
        "evaluation_runs",
        sa.Column("run_id",              postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",           sa.Text(), nullable=False),
        sa.Column("benchmark_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("learning.benchmark_suites.benchmark_id"), nullable=True),
        sa.Column("evaluation_type",     sa.Text(), nullable=False),
        sa.Column("prompt_version",      sa.Text(), nullable=True),
        sa.Column("model_config",        sa.Text(), nullable=True),
        sa.Column("calibration_version", sa.Text(), nullable=True),
        sa.Column("status",              sa.Text(), nullable=False, server_default="pending"),
        sa.Column("started_at",          sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("finished_at",         sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("case_count",          sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_reasoning_score", sa.Numeric(6, 4), nullable=True),
        sa.Column("outcome_accuracy",    sa.Numeric(6, 4), nullable=True),
        sa.Column("hallucination_rate",  sa.Numeric(6, 4), nullable=True),
        sa.Column("avg_latency_seconds", sa.Numeric(8, 3), nullable=True),
        sa.Column("aggregate",           postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("content_hash",        sa.Text(), nullable=False, server_default=""),
        sa.Column("triggered_by",        sa.Text(), nullable=False, server_default="system"),
        sa.Column("run_config",          postgresql.JSONB(), nullable=False, server_default="{}"),
        schema="learning",
    )
    op.create_index("idx_eval_runs_tenant",
                    "evaluation_runs", ["tenant_id", sa.text("started_at DESC")],
                    schema="learning")

    # ── 8. experiments ────────────────────────────────────────────────────────
    op.create_table(
        "experiments",
        sa.Column("experiment_id",      postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",          sa.Text(), nullable=False),
        sa.Column("slot",               sa.Text(), nullable=False),
        sa.Column("name",               sa.Text(), nullable=False),
        sa.Column("experiment_type",    sa.Text(), nullable=False),
        sa.Column("description",        sa.Text(), nullable=False, server_default=""),
        sa.Column("hypothesis",         sa.Text(), nullable=False, server_default=""),
        sa.Column("benchmark_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("learning.benchmark_suites.benchmark_id"), nullable=True),
        sa.Column("success_criteria",   postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("control_config",     postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("treatment_config",   postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("state",              sa.Text(), nullable=False, server_default="pending"),
        sa.Column("traffic_fraction",   sa.Numeric(5, 4), nullable=False, server_default="0.10"),
        sa.Column("success_metric",     sa.Text(), nullable=False, server_default="outcome_accuracy"),
        sa.Column("min_detectable_effect", sa.Numeric(6, 4), nullable=False, server_default="0.02"),
        sa.Column("created_at",         sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_by",         sa.Text(), nullable=False),
        sa.Column("approved_by",        sa.Text(), nullable=True),
        sa.Column("start_at",           sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("stop_at",            sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("concluded_at",       sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("conclusion",         sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata",           postgresql.JSONB(), nullable=False, server_default="{}"),
        schema="learning",
    )
    op.create_index("idx_experiments_tenant",
                    "experiments", ["tenant_id", "state"], schema="learning")

    # ── 9. experiment_runs ────────────────────────────────────────────────────
    op.create_table(
        "experiment_runs",
        sa.Column("run_id",          postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("experiment_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("learning.experiments.experiment_id"), nullable=False),
        sa.Column("tenant_id",       sa.Text(), nullable=False),
        sa.Column("snapshot",        postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("benchmark_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("learning.benchmark_suites.benchmark_id"), nullable=True),
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("learning.evaluation_runs.run_id"), nullable=True),
        sa.Column("status",          sa.Text(), nullable=False, server_default="running"),
        sa.Column("started_at",      sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at",    sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("summary_metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("notes",           sa.Text(), nullable=False, server_default=""),
        schema="learning",
    )
    op.create_index("idx_exp_runs_experiment",
                    "experiment_runs", ["experiment_id"], schema="learning")

    # ── 10. approval_requests ─────────────────────────────────────────────────
    op.create_table(
        "approval_requests",
        sa.Column("request_id",      postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",       sa.Text(), nullable=False),
        sa.Column("change_type",     sa.Text(), nullable=False),
        sa.Column("title",           sa.Text(), nullable=False),
        sa.Column("description",     sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_by",    sa.Text(), nullable=False),
        sa.Column("artifact_id",     sa.Text(), nullable=False),
        sa.Column("artifact_type",   sa.Text(), nullable=True),
        sa.Column("change_payload",  postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status",          sa.Text(), nullable=False, server_default="pending"),
        sa.Column("min_approvers",   sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at",      sa.TIMESTAMP(timezone=True), nullable=False),
        schema="learning",
    )
    op.create_index("idx_approvals_tenant_status",
                    "approval_requests", ["tenant_id", "status"], schema="learning")
    op.create_index("idx_approvals_artifact",
                    "approval_requests", ["artifact_id"], schema="learning")

    # ── 11. approval_decisions ────────────────────────────────────────────────
    op.create_table(
        "approval_decisions",
        sa.Column("decision_id",  postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("learning.approval_requests.request_id"), nullable=False),
        sa.Column("reviewer",     sa.Text(), nullable=False),
        sa.Column("decision",     sa.Text(), nullable=False),
        sa.Column("rationale",    sa.Text(), nullable=False, server_default=""),
        sa.Column("decided_at",   sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("content_hash", sa.Text(), nullable=False),
        schema="learning",
    )
    op.create_index("idx_decisions_request",
                    "approval_decisions", ["request_id"], schema="learning")

    # ── 12. memory_entries ────────────────────────────────────────────────────
    op.create_table(
        "memory_entries",
        sa.Column("entry_id",      postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",     sa.Text(), nullable=False),
        sa.Column("memory_type",   sa.Text(), nullable=False),
        sa.Column("content",       postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("content_hash",  sa.Text(), nullable=False),
        sa.Column("recorded_at",   sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("recorded_by",   sa.Text(), nullable=False),
        sa.Column("expires_at",    sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("tags",          postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default="{}"),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("artifact_id",   sa.Text(), nullable=True),
        schema="learning",
    )
    op.execute("""
        ALTER TABLE learning.memory_entries
        ADD CONSTRAINT fk_memory_supersedes FOREIGN KEY (supersedes_id)
        REFERENCES learning.memory_entries(entry_id)
    """)
    op.create_index("idx_memory_tenant_type",
                    "memory_entries", ["tenant_id", "memory_type",
                                       sa.text("recorded_at DESC")], schema="learning")
    op.create_index("idx_memory_artifact",
                    "memory_entries", ["tenant_id", "artifact_id"], schema="learning")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_tags
        ON learning.memory_entries USING GIN (tags)
    """)

    # ── 13. governance_audit ─────────────────────────────────────────────────
    op.create_table(
        "governance_audit",
        sa.Column("audit_id",      postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",     sa.Text(), nullable=False),
        sa.Column("event_type",    sa.Text(), nullable=False),
        sa.Column("actor",         sa.Text(), nullable=False),
        sa.Column("artifact_id",   sa.Text(), nullable=True),
        sa.Column("artifact_type", sa.Text(), nullable=True),
        sa.Column("payload",       postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("occurred_at",   sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("content_hash",  sa.Text(), nullable=False),
        sa.Column("prior_hash",    sa.Text(), nullable=True),
        sa.Column("chain_hash",    sa.Text(), nullable=False),
        sa.Column("source_ip",     sa.Text(), nullable=True),
        sa.Column("session_id",    sa.Text(), nullable=True),
        schema="learning",
    )
    op.create_index("idx_gov_audit_tenant",
                    "governance_audit", ["tenant_id", sa.text("occurred_at DESC")],
                    schema="learning")
    op.create_index("idx_gov_audit_actor",
                    "governance_audit", ["tenant_id", "actor"], schema="learning")

    # ── 14. recommendation_records ────────────────────────────────────────────
    op.create_table(
        "recommendation_records",
        sa.Column("rec_id",              postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",           sa.Text(), nullable=False),
        sa.Column("case_id",             sa.Text(), nullable=False),
        sa.Column("recommendation_type", sa.Text(), nullable=False),
        sa.Column("version",             sa.Text(), nullable=False),
        sa.Column("content",             postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("events",              postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("generated_at",        sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("generated_by",        sa.Text(), nullable=False, server_default="system"),
        sa.Column("outcome",             sa.Text(), nullable=True),
        sa.Column("was_followed",        sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("time_to_decision_hours", sa.Numeric(8, 2), nullable=True),
        schema="learning",
    )
    op.create_index("idx_recs_tenant_case",
                    "recommendation_records", ["tenant_id", "case_id"], schema="learning")
    op.create_index("idx_recs_tenant_type_version",
                    "recommendation_records",
                    ["tenant_id", "recommendation_type", "version"], schema="learning")

    # ── purge_expired_memory function ──────────────────────────────────────────
    op.execute("""
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
        $$ LANGUAGE plpgsql
    """)


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP FUNCTION IF EXISTS learning.purge_expired_memory(TEXT)")
    op.execute("DROP TABLE IF EXISTS learning.recommendation_records CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.governance_audit CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.memory_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.approval_decisions CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.approval_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.experiment_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.experiments CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.evaluation_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.benchmark_suites CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.workflow_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.prompt_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.calibration_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.feedback_lineage CASCADE")
    op.execute("DROP TABLE IF EXISTS learning.feedback_records CASCADE")
    op.execute("DROP FUNCTION IF EXISTS learning.touch_updated_at()")
    op.execute("DROP SCHEMA IF EXISTS learning CASCADE")
