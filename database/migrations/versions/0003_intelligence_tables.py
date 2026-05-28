"""Phase 7 — Enterprise Intelligence & Continuous Compliance Layer

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-25

Adds:
  audit.monitoring_runs          — scheduled/manual monitoring execution records
  audit.compliance_trends        — rolling window trend data per entity × rule
  audit.entity_risk_scores       — daily rolling risk scores
  audit.cross_case_correlations  — cross-case intelligence links
  audit.intelligence_graph_edges — compliance knowledge graph adjacency
  audit.copilot_sessions         — investigator copilot sessions (read-only)
  audit.analyst_overrides        — false positive and override tracking
  audit.evaluation_runs          — advanced evaluation framework persistence
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. monitoring_runs
    op.create_table(
        "monitoring_runs",
        sa.Column("run_id",             postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_type",           sa.String(50),  nullable=False),
        sa.Column("window_type",        sa.String(20),  nullable=False),
        sa.Column("window_start",       sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end",         sa.DateTime(timezone=True), nullable=False),
        sa.Column("status",             sa.String(20),  nullable=False, server_default="running"),
        sa.Column("findings_evaluated", sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("new_findings",       sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("drifts_detected",    sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("correlations_found", sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("run_metadata",       postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error_message",      sa.Text()),
        sa.Column("started_at",         sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at",       sa.DateTime(timezone=True)),
        schema="audit",
    )
    op.create_index("idx_monitoring_runs_status",      "monitoring_runs", ["status"],                       schema="audit")
    op.create_index("idx_monitoring_runs_started_at",  "monitoring_runs", [sa.text("started_at DESC")],     schema="audit")
    op.create_index("idx_monitoring_runs_window_type", "monitoring_runs", ["window_type", "window_start"],  schema="audit")

    # 2. compliance_trends
    op.create_table(
        "compliance_trends",
        sa.Column("trend_id",           postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entity_id",          postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type",        sa.String(50),  nullable=False),
        sa.Column("rule_code",          sa.String(20)),
        sa.Column("window_type",        sa.String(20),  nullable=False),
        sa.Column("window_start",       sa.Date(),      nullable=False),
        sa.Column("window_end",         sa.Date(),      nullable=False),
        sa.Column("finding_count",      sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("critical_count",     sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("high_count",         sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("financial_exposure", sa.Numeric(15, 2)),
        sa.Column("risk_score",         sa.Numeric(5, 4)),
        sa.Column("trend_direction",    sa.String(20)),
        sa.Column("velocity",           sa.Numeric(10, 4)),
        sa.Column("acceleration",       sa.Numeric(10, 4)),
        sa.Column("prior_period_count", sa.Integer()),
        sa.Column("computed_at",        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("monitoring_run_id",  postgresql.UUID(as_uuid=True), sa.ForeignKey("audit.monitoring_runs.run_id")),
        sa.UniqueConstraint("entity_id", "entity_type", "rule_code", "window_type", "window_start",
                            name="uq_compliance_trends_entity_window"),
        schema="audit",
    )
    op.create_index("idx_compliance_trends_entity", "compliance_trends", ["entity_id", "entity_type"], schema="audit")
    op.create_index("idx_compliance_trends_rule",   "compliance_trends", ["rule_code", "window_type"],  schema="audit")
    op.create_index("idx_compliance_trends_dir",    "compliance_trends", ["trend_direction", sa.text("computed_at DESC")], schema="audit")

    # 3. entity_risk_scores
    op.create_table(
        "entity_risk_scores",
        sa.Column("score_id",               postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entity_id",              postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type",            sa.String(50),  nullable=False),
        sa.Column("score_date",             sa.Date(),      nullable=False),
        sa.Column("composite_score",        sa.Numeric(5, 4), nullable=False),
        sa.Column("finding_velocity",       sa.Numeric(10, 4)),
        sa.Column("exposure_trajectory",    sa.Numeric(15, 2)),
        sa.Column("escalation_probability", sa.Numeric(5, 4)),
        sa.Column("trend_direction",        sa.String(20)),
        sa.Column("score_components",       postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("computed_at",            sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("entity_id", "entity_type", "score_date", name="uq_entity_risk_scores"),
        schema="audit",
    )
    op.create_index("idx_entity_risk_scores_entity", "entity_risk_scores", ["entity_id", "entity_type"], schema="audit")
    op.create_index("idx_entity_risk_scores_date",   "entity_risk_scores", [sa.text("score_date DESC")], schema="audit")
    op.create_index("idx_entity_risk_scores_score",  "entity_risk_scores", [sa.text("composite_score DESC")], schema="audit")

    # 4. cross_case_correlations
    op.create_table(
        "cross_case_correlations",
        sa.Column("correlation_id",    postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("case_id_a",         postgresql.UUID(as_uuid=True), sa.ForeignKey("audit.investigation_cases.case_id"), nullable=False),
        sa.Column("case_id_b",         postgresql.UUID(as_uuid=True), sa.ForeignKey("audit.investigation_cases.case_id"), nullable=False),
        sa.Column("correlation_type",  sa.String(50),  nullable=False),
        sa.Column("strength",          sa.Numeric(5, 4), nullable=False),
        sa.Column("shared_entities",   postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("explanation",       sa.Text()),
        sa.Column("detected_at",       sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("monitoring_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("audit.monitoring_runs.run_id")),
        sa.UniqueConstraint("case_id_a", "case_id_b", "correlation_type", name="uq_cross_case_correlations"),
        schema="audit",
    )
    op.create_index("idx_cross_case_corr_a",        "cross_case_correlations", ["case_id_a"],                              schema="audit")
    op.create_index("idx_cross_case_corr_b",        "cross_case_correlations", ["case_id_b"],                              schema="audit")
    op.create_index("idx_cross_case_corr_type",     "cross_case_correlations", ["correlation_type", sa.text("strength DESC")], schema="audit")

    # 5. intelligence_graph_edges
    op.create_table(
        "intelligence_graph_edges",
        sa.Column("edge_id",      postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_type",  sa.String(50),  nullable=False),
        sa.Column("source_id",    sa.String(255), nullable=False),
        sa.Column("target_type",  sa.String(50),  nullable=False),
        sa.Column("target_id",    sa.String(255), nullable=False),
        sa.Column("relationship", sa.String(50),  nullable=False),
        sa.Column("weight",       sa.Numeric(8, 4), nullable=False, server_default="1.0"),
        sa.Column("properties",   postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("valid_from",   sa.Date(),      nullable=False),
        sa.Column("valid_to",     sa.Date()),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_type", "source_id", "target_type", "target_id", "relationship",
                            name="uq_intelligence_graph_edges"),
        schema="audit",
    )
    op.create_index("idx_graph_edges_source", "intelligence_graph_edges", ["source_type", "source_id"], schema="audit")
    op.create_index("idx_graph_edges_target", "intelligence_graph_edges", ["target_type", "target_id"], schema="audit")
    op.create_index("idx_graph_edges_rel",    "intelligence_graph_edges", ["relationship"],              schema="audit")

    # 6. copilot_sessions
    op.create_table(
        "copilot_sessions",
        sa.Column("session_id",      postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("case_id",         postgresql.UUID(as_uuid=True), sa.ForeignKey("audit.investigation_cases.case_id"), nullable=False),
        sa.Column("investigator_id", sa.String(255)),
        sa.Column("session_type",    sa.String(50),  nullable=False),
        sa.Column("input_context",   postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("output",          postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("model_id",        sa.String(100)),
        sa.Column("input_tokens",    sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens",   sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_tokens",    sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms",      sa.Numeric(10, 2)),
        sa.Column("confidence_score",sa.Numeric(5, 4)),
        sa.Column("created_at",      sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="audit",
    )
    op.create_index("idx_copilot_sessions_case", "copilot_sessions", ["case_id", sa.text("created_at DESC")], schema="audit")
    op.create_index("idx_copilot_sessions_type", "copilot_sessions", ["session_type"],                        schema="audit")

    # 7. analyst_overrides
    op.create_table(
        "analyst_overrides",
        sa.Column("override_id",    postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("finding_id",     postgresql.UUID(as_uuid=True), sa.ForeignKey("audit.audit_findings.finding_id")),
        sa.Column("case_id",        postgresql.UUID(as_uuid=True), sa.ForeignKey("audit.investigation_cases.case_id")),
        sa.Column("analyst_id",     sa.String(255), nullable=False),
        sa.Column("override_type",  sa.String(50),  nullable=False),
        sa.Column("original_value", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("override_value", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("rationale",      sa.Text()),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="audit",
    )
    op.create_index("idx_analyst_overrides_finding", "analyst_overrides", ["finding_id"], schema="audit")
    op.create_index("idx_analyst_overrides_case",    "analyst_overrides", ["case_id"],    schema="audit")
    op.create_index("idx_analyst_overrides_type",    "analyst_overrides", ["override_type", sa.text("created_at DESC")], schema="audit")

    # 8. evaluation_runs
    op.create_table(
        "evaluation_runs",
        sa.Column("eval_id",       postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("eval_type",     sa.String(50),  nullable=False),
        sa.Column("eval_name",     sa.String(255)),
        sa.Column("status",        sa.String(20),  nullable=False, server_default="running"),
        sa.Column("passed",        sa.Boolean()),
        sa.Column("total_checks",  sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_checks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eval_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at",  sa.DateTime(timezone=True)),
        schema="audit",
    )
    op.create_index("idx_eval_runs_type",   "evaluation_runs", ["eval_type", sa.text("started_at DESC")], schema="audit")
    op.create_index("idx_eval_runs_status", "evaluation_runs", ["status"],                                 schema="audit")


def downgrade() -> None:
    for table in [
        "evaluation_runs", "analyst_overrides", "copilot_sessions",
        "intelligence_graph_edges", "cross_case_correlations",
        "entity_risk_scores", "compliance_trends", "monitoring_runs",
    ]:
        op.drop_table(table, schema="audit")
