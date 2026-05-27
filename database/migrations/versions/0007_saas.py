"""
Phase 12 — Multi-Tenant Enterprise SaaS tables.

Revision:      0007
Down revision: 0006
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision      = "0007"
down_revision = "0006"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── Schema ────────────────────────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS saas")

    # ── Tenancy ───────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("tenant_id",            sa.Text,  primary_key=True),
        sa.Column("name",                 sa.Text,  nullable=False),
        sa.Column("slug",                 sa.Text,  nullable=False, unique=True),
        sa.Column("status",               sa.Text,  nullable=False, server_default="provisioning"),
        sa.Column("tier",                 sa.Text,  nullable=False, server_default="starter"),
        sa.Column("plan_id",              sa.Text),
        sa.Column("parent_tenant_id",     sa.Text,  sa.ForeignKey("saas.tenants.tenant_id")),
        sa.Column("primary_contact_email",sa.Text),
        sa.Column("primary_contact_name", sa.Text),
        sa.Column("region",               sa.Text,  nullable=False, server_default="us-east-1"),
        sa.Column("created_at",           sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("activated_at",         sa.TIMESTAMP(timezone=True)),
        sa.Column("suspended_at",         sa.TIMESTAMP(timezone=True)),
        sa.Column("archived_at",          sa.TIMESTAMP(timezone=True)),
        sa.Column("metadata",             postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )
    op.create_index("idx_tenants_status", "tenants", ["status"], schema="saas")
    op.create_index("idx_tenants_tier",   "tenants", ["tier"],   schema="saas")

    op.create_table(
        "organizations",
        sa.Column("org_id",        sa.Text, primary_key=True),
        sa.Column("tenant_id",     sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("parent_org_id", sa.Text, sa.ForeignKey("saas.organizations.org_id")),
        sa.Column("name",          sa.Text, nullable=False),
        sa.Column("org_type",      sa.Text, nullable=False),
        sa.Column("region",        sa.Text),
        sa.Column("active",        sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at",    sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("metadata",      postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )
    op.create_index("idx_orgs_tenant", "organizations", ["tenant_id"], schema="saas")
    op.create_index("idx_orgs_parent", "organizations", ["parent_org_id"], schema="saas")

    op.create_table(
        "feature_flags",
        sa.Column("flag_id",   sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("flag_name", sa.Text, nullable=False),
        sa.Column("enabled",   sa.Boolean, nullable=False, server_default="false"),
        sa.Column("set_by",    sa.Text, nullable=False),
        sa.Column("set_at",    sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "flag_name"),
        schema="saas",
    )

    # ── Admin ─────────────────────────────────────────────────────────────────
    op.create_table(
        "admin_audit_log",
        sa.Column("record_id",    sa.Text, primary_key=True),
        sa.Column("tenant_id",    sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("event_type",   sa.Text, nullable=False),
        sa.Column("actor_id",     sa.Text, nullable=False),
        sa.Column("actor_role",   sa.Text),
        sa.Column("target_id",    sa.Text),
        sa.Column("target_type",  sa.Text),
        sa.Column("org_id",       sa.Text),
        sa.Column("payload",      postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("occurred_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        schema="saas",
    )
    op.create_index("idx_admin_audit_tenant", "admin_audit_log", ["tenant_id", "occurred_at"], schema="saas")
    op.create_index("idx_admin_audit_type",   "admin_audit_log", ["event_type"], schema="saas")

    op.create_table(
        "tenant_config",
        sa.Column("entry_id",      sa.Text, primary_key=True),
        sa.Column("tenant_id",     sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("namespace",     sa.Text, nullable=False),
        sa.Column("key",           sa.Text, nullable=False),
        sa.Column("value",         postgresql.JSONB),
        sa.Column("version",       sa.Integer, nullable=False, server_default="1"),
        sa.Column("changed_by",    sa.Text, nullable=False),
        sa.Column("changed_at",    sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("change_reason", sa.Text),
        sa.Column("content_hash",  sa.Text, nullable=False),
        sa.Column("superseded",    sa.Boolean, nullable=False, server_default="false"),
        schema="saas",
    )
    op.create_index("idx_config_tenant_key", "tenant_config", ["tenant_id", "namespace", "key"], schema="saas")

    # ── Config ────────────────────────────────────────────────────────────────
    op.create_table(
        "policy_overrides",
        sa.Column("override_id",     sa.Text, primary_key=True),
        sa.Column("tenant_id",       sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("name",            sa.Text, nullable=False),
        sa.Column("scope",           sa.Text, nullable=False),
        sa.Column("scope_key",       sa.Text, nullable=False),
        sa.Column("policy_config",   postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status",          sa.Text, nullable=False, server_default="active"),
        sa.Column("created_by",      sa.Text, nullable=False),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("effective_from",  sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("effective_until", sa.TIMESTAMP(timezone=True)),
        sa.Column("org_ids",         postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("version",         sa.Integer, nullable=False, server_default="1"),
        sa.Column("metadata",        postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )

    op.create_table(
        "payer_compliance_configs",
        sa.Column("config_id",               sa.Text, primary_key=True),
        sa.Column("tenant_id",               sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("payer_id",                sa.Text, nullable=False),
        sa.Column("payer_name",              sa.Text, nullable=False),
        sa.Column("detection_adjustments",   postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("audit_format",            sa.Text, nullable=False, server_default="cms_standard"),
        sa.Column("reporting_requirements",  postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("active",                  sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at",              sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "payer_id"),
        schema="saas",
    )

    # ── Billing ───────────────────────────────────────────────────────────────
    op.create_table(
        "billing_periods",
        sa.Column("period_id",    sa.Text, primary_key=True),
        sa.Column("tenant_id",    sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("year",         sa.Integer, nullable=False),
        sa.Column("month",        sa.Integer, nullable=False),
        sa.Column("status",       sa.Text, nullable=False, server_default="open"),
        sa.Column("opened_at",    sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("finalised_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("tenant_id", "year", "month"),
        schema="saas",
    )

    op.create_table(
        "usage_events",
        sa.Column("event_id",    sa.Text, primary_key=True),
        sa.Column("tenant_id",   sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("event_type",  sa.Text, nullable=False),
        sa.Column("quantity",    sa.Numeric, nullable=False),
        sa.Column("unit",        sa.Text, nullable=False),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("org_id",      sa.Text),
        sa.Column("entity_id",   sa.Text),
        sa.Column("model_id",    sa.Text),
        sa.Column("metadata",    postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )
    op.create_index("idx_usage_tenant_time", "usage_events", ["tenant_id", "occurred_at"], schema="saas")

    op.create_table(
        "usage_summaries",
        sa.Column("summary_id",       sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id",        sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("period_year",      sa.Integer, nullable=False),
        sa.Column("period_month",     sa.Integer, nullable=False),
        sa.Column("event_type",       sa.Text, nullable=False),
        sa.Column("total_quantity",   sa.Numeric, nullable=False, server_default="0"),
        sa.Column("event_count",      sa.Integer, nullable=False, server_default="0"),
        sa.Column("org_breakdown",    postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("entity_breakdown", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("model_breakdown",  postgresql.JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("tenant_id", "period_year", "period_month", "event_type"),
        schema="saas",
    )

    # ── Marketplace ───────────────────────────────────────────────────────────
    op.create_table(
        "workflow_templates",
        sa.Column("template_id",         sa.Text, primary_key=True),
        sa.Column("name",                sa.Text, nullable=False),
        sa.Column("version",             sa.Text, nullable=False),
        sa.Column("template_type",       sa.Text, nullable=False),
        sa.Column("title",               sa.Text, nullable=False),
        sa.Column("description",         sa.Text, nullable=False, server_default=""),
        sa.Column("workflow_definition", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status",              sa.Text, nullable=False, server_default="draft"),
        sa.Column("visibility",          sa.Text, nullable=False, server_default="public"),
        sa.Column("content_hash",        sa.Text, nullable=False),
        sa.Column("publisher_tenant_id", sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("created_at",          sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("created_by",          sa.Text, nullable=False),
        sa.Column("published_at",        sa.TIMESTAMP(timezone=True)),
        sa.Column("tags",                postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("compatible_tiers",    postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("install_count",       sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_rating",          sa.Numeric(3, 2)),
        sa.Column("allowed_tenant_ids",  postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("metadata",            postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )
    op.execute(
        "ALTER TABLE saas.workflow_templates "
        "ADD COLUMN parent_template_id TEXT REFERENCES saas.workflow_templates(template_id)"
    )
    op.create_index("idx_templates_publisher", "workflow_templates", ["publisher_tenant_id", "status"], schema="saas")

    op.create_table(
        "playbook_entries",
        sa.Column("entry_id",         sa.Text, primary_key=True),
        sa.Column("tenant_id",        sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("template_id",      sa.Text, sa.ForeignKey("saas.workflow_templates.template_id"), nullable=False),
        sa.Column("template_version", sa.Text, nullable=False),
        sa.Column("name",             sa.Text, nullable=False),
        sa.Column("active",           sa.Boolean, nullable=False, server_default="true"),
        sa.Column("installed_at",     sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("installed_by",     sa.Text, nullable=False, server_default="system"),
        sa.Column("custom_config",    postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("org_id",           sa.Text, sa.ForeignKey("saas.organizations.org_id")),
        schema="saas",
    )
    op.create_index("idx_playbooks_tenant", "playbook_entries", ["tenant_id", "active"], schema="saas")

    op.create_table(
        "publishing_requests",
        sa.Column("request_id",   sa.Text, primary_key=True),
        sa.Column("template_id",  sa.Text, sa.ForeignKey("saas.workflow_templates.template_id"), nullable=False),
        sa.Column("submitted_by", sa.Text, nullable=False),
        sa.Column("tenant_id",    sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("status",       sa.Text, nullable=False, server_default="pending"),
        sa.Column("reviewer_id",  sa.Text),
        sa.Column("reviewed_at",  sa.TIMESTAMP(timezone=True)),
        sa.Column("review_notes", sa.Text, nullable=False, server_default=""),
        sa.Column("content_hash", sa.Text, nullable=False),
        schema="saas",
    )

    op.create_table(
        "template_ratings",
        sa.Column("rating_id",   sa.Text, primary_key=True),
        sa.Column("tenant_id",   sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("template_id", sa.Text, sa.ForeignKey("saas.workflow_templates.template_id"), nullable=False),
        sa.Column("score",       sa.SmallInteger, nullable=False),
        sa.Column("review",      sa.Text, nullable=False, server_default=""),
        sa.Column("rated_at",    sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("rated_by",    sa.Text, nullable=False),
        sa.UniqueConstraint("tenant_id", "template_id"),
        schema="saas",
    )

    op.create_table(
        "upgrade_notifications",
        sa.Column("notification_id", sa.Text, primary_key=True),
        sa.Column("tenant_id",       sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("entry_id",        sa.Text, sa.ForeignKey("saas.playbook_entries.entry_id"), nullable=False),
        sa.Column("current_version", sa.Text, nullable=False),
        sa.Column("new_template_id", sa.Text, sa.ForeignKey("saas.workflow_templates.template_id"), nullable=False),
        sa.Column("new_version",     sa.Text, nullable=False),
        sa.Column("change_summary",  sa.Text, nullable=False, server_default=""),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("acknowledged",    sa.Boolean, nullable=False, server_default="false"),
        schema="saas",
    )

    # ── Notifications ─────────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("notification_id",   sa.Text, primary_key=True),
        sa.Column("tenant_id",         sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("recipient_id",      sa.Text, nullable=False),
        sa.Column("notification_type", sa.Text, nullable=False),
        sa.Column("title",             sa.Text, nullable=False),
        sa.Column("body",              sa.Text, nullable=False),
        sa.Column("priority",          sa.Text, nullable=False, server_default="normal"),
        sa.Column("channel",           sa.Text, nullable=False, server_default="in_app"),
        sa.Column("status",            sa.Text, nullable=False, server_default="pending"),
        sa.Column("created_at",        sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("sent_at",           sa.TIMESTAMP(timezone=True)),
        sa.Column("read_at",           sa.TIMESTAMP(timezone=True)),
        sa.Column("expires_at",        sa.TIMESTAMP(timezone=True)),
        sa.Column("reference_id",      sa.Text),
        sa.Column("reference_type",    sa.Text),
        sa.Column("metadata",          postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )
    op.create_index("idx_notifications_recipient", "notifications", ["tenant_id", "recipient_id", "status"], schema="saas")

    op.create_table(
        "notification_preferences",
        sa.Column("preference_id",     sa.Text, primary_key=True),
        sa.Column("tenant_id",         sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("user_id",           sa.Text, nullable=False),
        sa.Column("notification_type", sa.Text, nullable=False),
        sa.Column("channels",          postgresql.JSONB, nullable=False, server_default='["in_app"]'),
        sa.Column("enabled",           sa.Boolean, nullable=False, server_default="true"),
        sa.Column("quiet_start_utc",   sa.SmallInteger),
        sa.Column("quiet_end_utc",     sa.SmallInteger),
        sa.Column("updated_at",        sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "user_id", "notification_type"),
        schema="saas",
    )

    # ── Collaboration ─────────────────────────────────────────────────────────
    op.create_table(
        "investigation_assignments",
        sa.Column("assignment_id",       sa.Text, primary_key=True),
        sa.Column("tenant_id",           sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("investigation_id",    sa.Text, nullable=False),
        sa.Column("assigned_by",         sa.Text, nullable=False),
        sa.Column("assignee_id",         sa.Text, nullable=False),
        sa.Column("org_id",              sa.Text),
        sa.Column("status",              sa.Text, nullable=False, server_default="open"),
        sa.Column("notes",               sa.Text, nullable=False, server_default=""),
        sa.Column("due_at",              sa.TIMESTAMP(timezone=True)),
        sa.Column("assigned_at",         sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("accepted_at",         sa.TIMESTAMP(timezone=True)),
        sa.Column("completed_at",        sa.TIMESTAMP(timezone=True)),
        schema="saas",
    )
    op.execute(
        "ALTER TABLE saas.investigation_assignments "
        "ADD COLUMN prior_assignment_id TEXT REFERENCES saas.investigation_assignments(assignment_id)"
    )
    op.create_index("idx_assignments_assignee", "investigation_assignments", ["tenant_id", "assignee_id", "status"], schema="saas")

    op.create_table(
        "review_requests",
        sa.Column("review_id",        sa.Text, primary_key=True),
        sa.Column("tenant_id",        sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("investigation_id", sa.Text, nullable=False),
        sa.Column("requested_by",     sa.Text, nullable=False),
        sa.Column("reviewer_id",      sa.Text, nullable=False),
        sa.Column("reason",           sa.Text, nullable=False),
        sa.Column("status",           sa.Text, nullable=False, server_default="open"),
        sa.Column("outcome",          sa.Text),
        sa.Column("outcome_notes",    sa.Text, nullable=False, server_default=""),
        sa.Column("requested_at",     sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at",     sa.TIMESTAMP(timezone=True)),
        sa.Column("priority",         sa.Text, nullable=False, server_default="normal"),
        schema="saas",
    )

    # ── API ───────────────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("key_id",       sa.Text, primary_key=True),
        sa.Column("tenant_id",    sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("name",         sa.Text, nullable=False),
        sa.Column("key_hash",     sa.Text, nullable=False, unique=True),
        sa.Column("key_prefix",   sa.Text, nullable=False),
        sa.Column("status",       sa.Text, nullable=False, server_default="active"),
        sa.Column("scopes",       postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("org_id",       sa.Text),
        sa.Column("created_by",   sa.Text, nullable=False),
        sa.Column("created_at",   sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at",   sa.TIMESTAMP(timezone=True)),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("grace_until",  sa.TIMESTAMP(timezone=True)),
        sa.Column("metadata",     postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )
    op.execute(
        "ALTER TABLE saas.api_keys "
        "ADD COLUMN rotated_to TEXT REFERENCES saas.api_keys(key_id)"
    )
    op.create_index("idx_api_keys_tenant", "api_keys", ["tenant_id", "status"], schema="saas")
    op.create_index("idx_api_keys_hash",   "api_keys", ["key_hash"], schema="saas")

    op.create_table(
        "webhook_endpoints",
        sa.Column("endpoint_id",     sa.Text, primary_key=True),
        sa.Column("tenant_id",       sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("url",             sa.Text, nullable=False),
        sa.Column("secret_hash",     sa.Text, nullable=False),
        sa.Column("name",            sa.Text, nullable=False),
        sa.Column("event_types",     postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("active",          sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by",      sa.Text, nullable=False),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("failure_count",   sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("metadata",        postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )

    op.create_table(
        "webhook_delivery_attempts",
        sa.Column("attempt_id",    sa.Text, primary_key=True),
        sa.Column("event_id",      sa.Text, nullable=False),
        sa.Column("endpoint_id",   sa.Text, sa.ForeignKey("saas.webhook_endpoints.endpoint_id"), nullable=False),
        sa.Column("tenant_id",     sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("attempt_num",   sa.Integer, nullable=False, server_default="1"),
        sa.Column("status",        sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempted_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("response_code", sa.SmallInteger),
        sa.Column("error",         sa.Text),
        sa.Column("next_retry_at", sa.TIMESTAMP(timezone=True)),
        schema="saas",
    )

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    op.create_table(
        "onboarding_states",
        sa.Column("onboarding_id", sa.Text, primary_key=True),
        sa.Column("tenant_id",     sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False, unique=True),
        sa.Column("status",        sa.Text, nullable=False, server_default="in_progress"),
        sa.Column("steps",         postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("started_at",    sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at",  sa.TIMESTAMP(timezone=True)),
        sa.Column("due_by",        sa.TIMESTAMP(timezone=True)),
        schema="saas",
    )

    op.create_table(
        "archival_records",
        sa.Column("record_id",         sa.Text, primary_key=True),
        sa.Column("tenant_id",         sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("policy_id",         sa.Text, nullable=False),
        sa.Column("status",            sa.Text, nullable=False, server_default="scheduled"),
        sa.Column("reason",            sa.Text, nullable=False),
        sa.Column("retention_days",    sa.Integer, nullable=False),
        sa.Column("legal_hold",        sa.Boolean, nullable=False, server_default="false"),
        sa.Column("initiated_by",      sa.Text, nullable=False),
        sa.Column("initiated_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("archived_at",       sa.TIMESTAMP(timezone=True)),
        sa.Column("purge_eligible_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("purged_at",         sa.TIMESTAMP(timezone=True)),
        sa.Column("restored_at",       sa.TIMESTAMP(timezone=True)),
        sa.Column("storage_location",  sa.Text),
        sa.Column("metadata",          postgresql.JSONB, nullable=False, server_default="{}"),
        schema="saas",
    )

    # ── Governance ────────────────────────────────────────────────────────────
    op.create_table(
        "retention_policies",
        sa.Column("policy_id",      sa.Text, primary_key=True),
        sa.Column("tenant_id",      sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("category",       sa.Text, nullable=False),
        sa.Column("retention_days", sa.Integer, nullable=False),
        sa.Column("action",         sa.Text, nullable=False, server_default="archive"),
        sa.Column("created_by",     sa.Text, nullable=False),
        sa.Column("created_at",     sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("description",    sa.Text, nullable=False, server_default=""),
        sa.Column("active",         sa.Boolean, nullable=False, server_default="true"),
        sa.UniqueConstraint("tenant_id", "category"),
        schema="saas",
    )

    op.create_table(
        "legal_holds",
        sa.Column("hold_id",     sa.Text, primary_key=True),
        sa.Column("tenant_id",   sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("name",        sa.Text, nullable=False),
        sa.Column("scope_query", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("reason",      sa.Text, nullable=False),
        sa.Column("imposed_by",  sa.Text, nullable=False),
        sa.Column("imposed_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("released_by", sa.Text),
        schema="saas",
    )

    op.create_table(
        "org_governance_settings",
        sa.Column("settings_id",              sa.Text, primary_key=True),
        sa.Column("tenant_id",                sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False),
        sa.Column("org_id",                   sa.Text, sa.ForeignKey("saas.organizations.org_id"), nullable=False),
        sa.Column("version",                  sa.Integer, nullable=False, server_default="1"),
        sa.Column("min_reviewers",            sa.SmallInteger, nullable=False, server_default="1"),
        sa.Column("auto_escalate_hours",      sa.Integer, nullable=False, server_default="72"),
        sa.Column("second_review_threshold",  sa.Numeric(4, 3), nullable=False, server_default="0.800"),
        sa.Column("mandatory_fields",         postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("reporting_cadence_days",   sa.Integer, nullable=False, server_default="30"),
        sa.Column("allow_self_close",         sa.Boolean, nullable=False, server_default="false"),
        sa.Column("require_evidence_upload",  sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by",               sa.Text, nullable=False),
        sa.Column("created_at",               sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("superseded",               sa.Boolean, nullable=False, server_default="false"),
        sa.Column("content_hash",             sa.Text, nullable=False),
        sa.Column("notes",                    sa.Text, nullable=False, server_default=""),
        schema="saas",
    )

    # ── Scaling ───────────────────────────────────────────────────────────────
    op.create_table(
        "partition_assignments",
        sa.Column("assignment_id", sa.Text, primary_key=True),
        sa.Column("tenant_id",     sa.Text, sa.ForeignKey("saas.tenants.tenant_id"), nullable=False, unique=True),
        sa.Column("partition_id",  sa.Text, nullable=False),
        sa.Column("strategy",      sa.Text, nullable=False),
        sa.Column("queue_name",    sa.Text, nullable=False),
        sa.Column("dedicated",     sa.Boolean, nullable=False, server_default="false"),
        sa.Column("assigned_at",   sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        schema="saas",
    )

    op.create_table(
        "scaling_events",
        sa.Column("event_id",      sa.Text, primary_key=True),
        sa.Column("pool_name",     sa.Text, nullable=False),
        sa.Column("direction",     sa.Text, nullable=False),
        sa.Column("trigger",       sa.Text, nullable=False),
        sa.Column("from_replicas", sa.Integer, nullable=False),
        sa.Column("to_replicas",   sa.Integer, nullable=False),
        sa.Column("utilisation",   sa.Numeric(6, 4), nullable=False),
        sa.Column("reason",        sa.Text, nullable=False),
        sa.Column("decided_at",    sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("applied",       sa.Boolean, nullable=False, server_default="false"),
        sa.Column("apply_error",   sa.Text),
        schema="saas",
    )
    op.create_index("idx_scaling_events_pool", "scaling_events", ["pool_name", "decided_at"], schema="saas")


def downgrade() -> None:
    # Drop in reverse dependency order
    for tbl in [
        "scaling_events", "partition_assignments",
        "org_governance_settings", "legal_holds", "retention_policies",
        "archival_records", "onboarding_states",
        "webhook_delivery_attempts", "webhook_endpoints", "api_keys",
        "review_requests", "investigation_assignments",
        "notification_preferences", "notifications",
        "upgrade_notifications", "template_ratings",
        "publishing_requests", "playbook_entries", "workflow_templates",
        "usage_summaries", "usage_events", "billing_periods",
        "payer_compliance_configs", "policy_overrides",
        "tenant_config", "admin_audit_log",
        "feature_flags", "organizations", "tenants",
    ]:
        op.drop_table(tbl, schema="saas")
    op.execute("DROP SCHEMA IF EXISTS saas CASCADE")
