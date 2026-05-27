-- ============================================================================
-- 011_saas_tables.sql
-- Phase 12 — Multi-Tenant Enterprise SaaS schema
--
-- Schema: saas
-- Depends on: all prior migrations (001-010)
--
-- Table inventory (28 tables across 13 subpackages)
-- ─────────────────────────────────────────────────
-- tenancy:       tenants, organizations, feature_flags
-- admin:         admin_audit_log, provisioning_results, tenant_config
-- config:        rule_pack_assignments, policy_overrides, payer_configs
-- billing:       usage_events, billing_periods, usage_summaries
-- marketplace:   workflow_templates, playbook_entries, publishing_requests
--                template_ratings, upgrade_notifications
-- notifications: notifications, notification_preferences
-- collaboration: investigation_assignments, review_requests
-- api:           api_keys, webhook_endpoints, webhook_delivery_attempts
-- lifecycle:     onboarding_states, archival_records
-- governance:    retention_policies, legal_holds, org_governance_settings
-- scaling:       partition_assignments, scaling_events
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS saas;

-- ── Tenancy ──────────────────────────────────────────────────────────────────

CREATE TABLE saas.tenants (
    tenant_id        TEXT        PRIMARY KEY,
    name             TEXT        NOT NULL,
    slug             TEXT        NOT NULL UNIQUE,
    status           TEXT        NOT NULL DEFAULT 'provisioning',
    tier             TEXT        NOT NULL DEFAULT 'starter',
    plan_id          TEXT,
    parent_tenant_id TEXT        REFERENCES saas.tenants(tenant_id),
    primary_contact_email TEXT,
    primary_contact_name  TEXT,
    region           TEXT        NOT NULL DEFAULT 'us-east-1',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at     TIMESTAMPTZ,
    suspended_at     TIMESTAMPTZ,
    archived_at      TIMESTAMPTZ,
    metadata         JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_tenants_status ON saas.tenants (status);
CREATE INDEX idx_tenants_tier   ON saas.tenants (tier);

CREATE TABLE saas.organizations (
    org_id           TEXT        PRIMARY KEY,
    tenant_id        TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    parent_org_id    TEXT        REFERENCES saas.organizations(org_id),
    name             TEXT        NOT NULL,
    org_type         TEXT        NOT NULL,
    region           TEXT,
    active           BOOLEAN     NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata         JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_orgs_tenant    ON saas.organizations (tenant_id);
CREATE INDEX idx_orgs_parent    ON saas.organizations (parent_org_id);

CREATE TABLE saas.feature_flags (
    flag_id     BIGSERIAL   PRIMARY KEY,
    tenant_id   TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    flag_name   TEXT        NOT NULL,
    enabled     BOOLEAN     NOT NULL DEFAULT false,
    set_by      TEXT        NOT NULL,
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, flag_name)
);

-- ── Admin ─────────────────────────────────────────────────────────────────────

CREATE TABLE saas.admin_audit_log (
    record_id    TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    event_type   TEXT        NOT NULL,
    actor_id     TEXT        NOT NULL,
    actor_role   TEXT,
    target_id    TEXT,
    target_type  TEXT,
    org_id       TEXT,
    payload      JSONB       NOT NULL DEFAULT '{}',
    content_hash TEXT        NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_admin_audit_tenant  ON saas.admin_audit_log (tenant_id, occurred_at DESC);
CREATE INDEX idx_admin_audit_type    ON saas.admin_audit_log (event_type);
CREATE INDEX idx_admin_audit_actor   ON saas.admin_audit_log (actor_id);

CREATE TABLE saas.tenant_config (
    entry_id     TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    namespace    TEXT        NOT NULL,
    key          TEXT        NOT NULL,
    value        JSONB,
    version      INT         NOT NULL DEFAULT 1,
    changed_by   TEXT        NOT NULL,
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_reason TEXT,
    content_hash TEXT        NOT NULL,
    superseded   BOOLEAN     NOT NULL DEFAULT false
);

CREATE INDEX idx_config_tenant_key ON saas.tenant_config (tenant_id, namespace, key);
CREATE INDEX idx_config_active     ON saas.tenant_config (tenant_id, namespace, key)
    WHERE superseded = false;

-- ── Config ────────────────────────────────────────────────────────────────────

CREATE TABLE saas.policy_overrides (
    override_id     TEXT        PRIMARY KEY,
    tenant_id       TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    name            TEXT        NOT NULL,
    scope           TEXT        NOT NULL,
    scope_key       TEXT        NOT NULL,
    policy_config   JSONB       NOT NULL DEFAULT '{}',
    status          TEXT        NOT NULL DEFAULT 'active',
    created_by      TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_from  TIMESTAMPTZ NOT NULL,
    effective_until TIMESTAMPTZ,
    org_ids         JSONB       NOT NULL DEFAULT '[]',
    version         INT         NOT NULL DEFAULT 1,
    metadata        JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_overrides_tenant ON saas.policy_overrides (tenant_id, status);

CREATE TABLE saas.payer_compliance_configs (
    config_id        TEXT        PRIMARY KEY,
    tenant_id        TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    payer_id         TEXT        NOT NULL,
    payer_name       TEXT        NOT NULL,
    detection_adjustments JSONB  NOT NULL DEFAULT '{}',
    audit_format     TEXT        NOT NULL DEFAULT 'cms_standard',
    reporting_requirements JSONB NOT NULL DEFAULT '[]',
    active           BOOLEAN     NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, payer_id)
);

-- ── Billing ───────────────────────────────────────────────────────────────────

CREATE TABLE saas.billing_periods (
    period_id    TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    year         INT         NOT NULL,
    month        INT         NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'open',
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalised_at TIMESTAMPTZ,
    UNIQUE (tenant_id, year, month)
);

CREATE INDEX idx_billing_tenant ON saas.billing_periods (tenant_id, year DESC, month DESC);

CREATE TABLE saas.usage_events (
    event_id     TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    event_type   TEXT        NOT NULL,
    quantity     NUMERIC     NOT NULL,
    unit         TEXT        NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL,
    org_id       TEXT,
    entity_id    TEXT,
    model_id     TEXT,
    metadata     JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_usage_tenant_time ON saas.usage_events (tenant_id, occurred_at DESC);
CREATE INDEX idx_usage_type        ON saas.usage_events (tenant_id, event_type);

CREATE TABLE saas.usage_summaries (
    summary_id       BIGSERIAL   PRIMARY KEY,
    tenant_id        TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    period_year      INT         NOT NULL,
    period_month     INT         NOT NULL,
    event_type       TEXT        NOT NULL,
    total_quantity   NUMERIC     NOT NULL DEFAULT 0,
    event_count      INT         NOT NULL DEFAULT 0,
    org_breakdown    JSONB       NOT NULL DEFAULT '{}',
    entity_breakdown JSONB       NOT NULL DEFAULT '{}',
    model_breakdown  JSONB       NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, period_year, period_month, event_type)
);

-- ── Marketplace ───────────────────────────────────────────────────────────────

CREATE TABLE saas.workflow_templates (
    template_id          TEXT        PRIMARY KEY,
    name                 TEXT        NOT NULL,
    version              TEXT        NOT NULL,
    template_type        TEXT        NOT NULL,
    title                TEXT        NOT NULL,
    description          TEXT        NOT NULL DEFAULT '',
    workflow_definition  JSONB       NOT NULL DEFAULT '{}',
    status               TEXT        NOT NULL DEFAULT 'draft',
    visibility           TEXT        NOT NULL DEFAULT 'public',
    content_hash         TEXT        NOT NULL,
    publisher_tenant_id  TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by           TEXT        NOT NULL,
    published_at         TIMESTAMPTZ,
    tags                 JSONB       NOT NULL DEFAULT '[]',
    compatible_tiers     JSONB       NOT NULL DEFAULT '[]',
    install_count        INT         NOT NULL DEFAULT 0,
    avg_rating           NUMERIC(3,2),
    parent_template_id   TEXT        REFERENCES saas.workflow_templates(template_id),
    allowed_tenant_ids   JSONB       NOT NULL DEFAULT '[]',
    metadata             JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_templates_publisher ON saas.workflow_templates (publisher_tenant_id, status);
CREATE INDEX idx_templates_status    ON saas.workflow_templates (status) WHERE status = 'published';
CREATE INDEX idx_templates_name      ON saas.workflow_templates (name, version);
CREATE INDEX idx_templates_tags      ON saas.workflow_templates USING GIN (tags);

CREATE TABLE saas.playbook_entries (
    entry_id         TEXT        PRIMARY KEY,
    tenant_id        TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    template_id      TEXT        NOT NULL REFERENCES saas.workflow_templates(template_id),
    template_version TEXT        NOT NULL,
    name             TEXT        NOT NULL,
    active           BOOLEAN     NOT NULL DEFAULT true,
    installed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    installed_by     TEXT        NOT NULL DEFAULT 'system',
    custom_config    JSONB       NOT NULL DEFAULT '{}',
    org_id           TEXT        REFERENCES saas.organizations(org_id)
);

CREATE INDEX idx_playbooks_tenant ON saas.playbook_entries (tenant_id, active);

CREATE TABLE saas.publishing_requests (
    request_id   TEXT        PRIMARY KEY,
    template_id  TEXT        NOT NULL REFERENCES saas.workflow_templates(template_id),
    submitted_by TEXT        NOT NULL,
    tenant_id    TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT        NOT NULL DEFAULT 'pending',
    reviewer_id  TEXT,
    reviewed_at  TIMESTAMPTZ,
    review_notes TEXT        NOT NULL DEFAULT '',
    content_hash TEXT        NOT NULL
);

CREATE INDEX idx_pub_requests_status ON saas.publishing_requests (status)
    WHERE status = 'pending';

CREATE TABLE saas.template_ratings (
    rating_id    TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    template_id  TEXT        NOT NULL REFERENCES saas.workflow_templates(template_id),
    score        SMALLINT    NOT NULL CHECK (score BETWEEN 1 AND 5),
    review       TEXT        NOT NULL DEFAULT '',
    rated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    rated_by     TEXT        NOT NULL,
    UNIQUE (tenant_id, template_id)
);

CREATE TABLE saas.upgrade_notifications (
    notification_id TEXT        PRIMARY KEY,
    tenant_id       TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    entry_id        TEXT        NOT NULL REFERENCES saas.playbook_entries(entry_id),
    current_version TEXT        NOT NULL,
    new_template_id TEXT        NOT NULL REFERENCES saas.workflow_templates(template_id),
    new_version     TEXT        NOT NULL,
    change_summary  TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged    BOOLEAN     NOT NULL DEFAULT false
);

CREATE INDEX idx_upgrade_notif_tenant ON saas.upgrade_notifications (tenant_id, acknowledged);

-- ── Notifications ─────────────────────────────────────────────────────────────

CREATE TABLE saas.notifications (
    notification_id   TEXT        PRIMARY KEY,
    tenant_id         TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    recipient_id      TEXT        NOT NULL,
    notification_type TEXT        NOT NULL,
    title             TEXT        NOT NULL,
    body              TEXT        NOT NULL,
    priority          TEXT        NOT NULL DEFAULT 'normal',
    channel           TEXT        NOT NULL DEFAULT 'in_app',
    status            TEXT        NOT NULL DEFAULT 'pending',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at           TIMESTAMPTZ,
    read_at           TIMESTAMPTZ,
    expires_at        TIMESTAMPTZ,
    reference_id      TEXT,
    reference_type    TEXT,
    metadata          JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_notifications_recipient ON saas.notifications (tenant_id, recipient_id, status);
CREATE INDEX idx_notifications_unread    ON saas.notifications (tenant_id, recipient_id)
    WHERE status = 'sent' AND channel = 'in_app';

CREATE TABLE saas.notification_preferences (
    preference_id     TEXT        PRIMARY KEY,
    tenant_id         TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    user_id           TEXT        NOT NULL,
    notification_type TEXT        NOT NULL,
    channels          JSONB       NOT NULL DEFAULT '["in_app"]',
    enabled           BOOLEAN     NOT NULL DEFAULT true,
    quiet_start_utc   SMALLINT    CHECK (quiet_start_utc BETWEEN 0 AND 23),
    quiet_end_utc     SMALLINT    CHECK (quiet_end_utc BETWEEN 0 AND 23),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, notification_type)
);

-- ── Collaboration ─────────────────────────────────────────────────────────────

CREATE TABLE saas.investigation_assignments (
    assignment_id        TEXT        PRIMARY KEY,
    tenant_id            TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    investigation_id     TEXT        NOT NULL,
    assigned_by          TEXT        NOT NULL,
    assignee_id          TEXT        NOT NULL,
    org_id               TEXT,
    status               TEXT        NOT NULL DEFAULT 'open',
    notes                TEXT        NOT NULL DEFAULT '',
    due_at               TIMESTAMPTZ,
    assigned_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    accepted_at          TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    prior_assignment_id  TEXT        REFERENCES saas.investigation_assignments(assignment_id)
);

CREATE INDEX idx_assignments_assignee ON saas.investigation_assignments (tenant_id, assignee_id, status);
CREATE INDEX idx_assignments_inv      ON saas.investigation_assignments (tenant_id, investigation_id);

CREATE TABLE saas.review_requests (
    review_id        TEXT        PRIMARY KEY,
    tenant_id        TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    investigation_id TEXT        NOT NULL,
    requested_by     TEXT        NOT NULL,
    reviewer_id      TEXT        NOT NULL,
    reason           TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'open',
    outcome          TEXT,
    outcome_notes    TEXT        NOT NULL DEFAULT '',
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ,
    priority         TEXT        NOT NULL DEFAULT 'normal'
);

CREATE INDEX idx_reviews_reviewer ON saas.review_requests (tenant_id, reviewer_id, status);

-- ── API ───────────────────────────────────────────────────────────────────────

CREATE TABLE saas.api_keys (
    key_id        TEXT        PRIMARY KEY,
    tenant_id     TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    name          TEXT        NOT NULL,
    key_hash      TEXT        NOT NULL UNIQUE,
    key_prefix    TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'active',
    scopes        JSONB       NOT NULL DEFAULT '[]',
    org_id        TEXT,
    created_by    TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ,
    last_used_at  TIMESTAMPTZ,
    grace_until   TIMESTAMPTZ,
    rotated_to    TEXT        REFERENCES saas.api_keys(key_id),
    metadata      JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_api_keys_tenant ON saas.api_keys (tenant_id, status);
CREATE INDEX idx_api_keys_hash   ON saas.api_keys (key_hash);

CREATE TABLE saas.webhook_endpoints (
    endpoint_id    TEXT        PRIMARY KEY,
    tenant_id      TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    url            TEXT        NOT NULL,
    secret_hash    TEXT        NOT NULL,    -- SHA-256 of signing secret
    name           TEXT        NOT NULL,
    event_types    JSONB       NOT NULL DEFAULT '[]',
    active         BOOLEAN     NOT NULL DEFAULT true,
    created_by     TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    failure_count  INT         NOT NULL DEFAULT 0,
    last_success_at TIMESTAMPTZ,
    metadata       JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_webhooks_tenant ON saas.webhook_endpoints (tenant_id, active);

CREATE TABLE saas.webhook_delivery_attempts (
    attempt_id    TEXT        PRIMARY KEY,
    event_id      TEXT        NOT NULL,
    endpoint_id   TEXT        NOT NULL REFERENCES saas.webhook_endpoints(endpoint_id),
    tenant_id     TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    attempt_num   INT         NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL DEFAULT 'pending',
    attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    response_code SMALLINT,
    error         TEXT,
    next_retry_at TIMESTAMPTZ
);

CREATE INDEX idx_webhook_attempts_endpoint ON saas.webhook_delivery_attempts (endpoint_id, attempted_at DESC);
CREATE INDEX idx_webhook_retry             ON saas.webhook_delivery_attempts (next_retry_at)
    WHERE status = 'retrying';

-- ── Lifecycle ─────────────────────────────────────────────────────────────────

CREATE TABLE saas.onboarding_states (
    onboarding_id TEXT        PRIMARY KEY,
    tenant_id     TEXT        NOT NULL REFERENCES saas.tenants(tenant_id) UNIQUE,
    status        TEXT        NOT NULL DEFAULT 'in_progress',
    steps         JSONB       NOT NULL DEFAULT '[]',
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ,
    due_by        TIMESTAMPTZ
);

CREATE TABLE saas.archival_records (
    record_id           TEXT        PRIMARY KEY,
    tenant_id           TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    policy_id           TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'scheduled',
    reason              TEXT        NOT NULL,
    retention_days      INT         NOT NULL,
    legal_hold          BOOLEAN     NOT NULL DEFAULT false,
    initiated_by        TEXT        NOT NULL,
    initiated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at         TIMESTAMPTZ,
    purge_eligible_at   TIMESTAMPTZ,
    purged_at           TIMESTAMPTZ,
    restored_at         TIMESTAMPTZ,
    storage_location    TEXT,
    metadata            JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_archival_tenant ON saas.archival_records (tenant_id, status);
CREATE INDEX idx_archival_purge  ON saas.archival_records (purge_eligible_at)
    WHERE status = 'archived' AND legal_hold = false;

-- ── Governance ────────────────────────────────────────────────────────────────

CREATE TABLE saas.retention_policies (
    policy_id       TEXT        PRIMARY KEY,
    tenant_id       TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    category        TEXT        NOT NULL,
    retention_days  INT         NOT NULL,
    action          TEXT        NOT NULL DEFAULT 'archive',
    created_by      TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    description     TEXT        NOT NULL DEFAULT '',
    active          BOOLEAN     NOT NULL DEFAULT true,
    UNIQUE (tenant_id, category)
);

CREATE TABLE saas.legal_holds (
    hold_id      TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    name         TEXT        NOT NULL,
    scope_query  JSONB       NOT NULL DEFAULT '{}',
    reason       TEXT        NOT NULL,
    imposed_by   TEXT        NOT NULL,
    imposed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at  TIMESTAMPTZ,
    released_by  TEXT
);

CREATE INDEX idx_legal_holds_tenant ON saas.legal_holds (tenant_id, released_at)
    WHERE released_at IS NULL;

CREATE TABLE saas.org_governance_settings (
    settings_id              TEXT        PRIMARY KEY,
    tenant_id                TEXT        NOT NULL REFERENCES saas.tenants(tenant_id),
    org_id                   TEXT        NOT NULL REFERENCES saas.organizations(org_id),
    version                  INT         NOT NULL DEFAULT 1,
    min_reviewers            SMALLINT    NOT NULL DEFAULT 1,
    auto_escalate_hours      INT         NOT NULL DEFAULT 72,
    second_review_threshold  NUMERIC(4,3) NOT NULL DEFAULT 0.800,
    mandatory_fields         JSONB       NOT NULL DEFAULT '[]',
    reporting_cadence_days   INT         NOT NULL DEFAULT 30,
    allow_self_close         BOOLEAN     NOT NULL DEFAULT false,
    require_evidence_upload  BOOLEAN     NOT NULL DEFAULT true,
    created_by               TEXT        NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded               BOOLEAN     NOT NULL DEFAULT false,
    content_hash             TEXT        NOT NULL,
    notes                    TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX idx_gov_settings_active ON saas.org_governance_settings (tenant_id, org_id)
    WHERE superseded = false;

-- ── Scaling ───────────────────────────────────────────────────────────────────

CREATE TABLE saas.partition_assignments (
    assignment_id TEXT        PRIMARY KEY,
    tenant_id     TEXT        NOT NULL REFERENCES saas.tenants(tenant_id) UNIQUE,
    partition_id  TEXT        NOT NULL,
    strategy      TEXT        NOT NULL,
    queue_name    TEXT        NOT NULL,
    dedicated     BOOLEAN     NOT NULL DEFAULT false,
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE saas.scaling_events (
    event_id       TEXT        PRIMARY KEY,
    pool_name      TEXT        NOT NULL,
    direction      TEXT        NOT NULL,
    trigger        TEXT        NOT NULL,
    from_replicas  INT         NOT NULL,
    to_replicas    INT         NOT NULL,
    utilisation    NUMERIC(6,4) NOT NULL,
    reason         TEXT        NOT NULL,
    decided_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied        BOOLEAN     NOT NULL DEFAULT false,
    apply_error    TEXT
);

CREATE INDEX idx_scaling_events_pool ON saas.scaling_events (pool_name, decided_at DESC);
