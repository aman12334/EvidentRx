-- =============================================================================
-- Script 008: Authentication & User Tables (auth schema)
--
-- Phase 9 addition — supports RBAC, multi-tenant user management, and
-- session-level audit trails.
--
-- Creation order:
--   auth.users            (tenant-scoped user accounts)
--   auth.user_sessions    (refresh token audit log — distinct from in-memory store)
--   auth.password_history (prevents reuse within policy window)
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS auth;

-- =============================================================================
-- auth.users
-- Purpose     : Tenant-scoped user accounts with hashed credentials.
--               One user belongs to exactly one covered entity tenant.
--               Roles are cumulative: analyst < senior_analyst < auditor < admin < system.
-- Security    : hashed_password is ALWAYS bcrypt (rounds=12). Plaintext is never stored.
-- Multi-tenant: tenant_id FK → ref.covered_entities.ce_id enforces isolation.
-- =============================================================================
CREATE TABLE auth.users (
    user_id             UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity
    email               TEXT            NOT NULL,
    full_name           TEXT,
    hashed_password     TEXT            NOT NULL,

    -- RBAC
    role                VARCHAR(30)     NOT NULL DEFAULT 'analyst',

    -- Tenant binding
    tenant_id           UUID            NOT NULL REFERENCES ref.covered_entities(ce_id)
                                        ON DELETE RESTRICT,

    -- Lifecycle
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    is_verified         BOOLEAN         NOT NULL DEFAULT FALSE,
    force_password_reset BOOLEAN        NOT NULL DEFAULT FALSE,
    last_login_at       TIMESTAMPTZ,
    failed_login_count  INTEGER         NOT NULL DEFAULT 0,
    locked_until        TIMESTAMPTZ,

    -- Audit
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by          UUID,           -- user_id of admin who created this account
    deactivated_at      TIMESTAMPTZ,

    -- Constraints
    CONSTRAINT uq_users_email_tenant UNIQUE (email, tenant_id),
    CONSTRAINT ck_users_role CHECK (role IN (
        'analyst', 'senior_analyst', 'auditor', 'admin', 'system'
    )),
    CONSTRAINT ck_users_email_format CHECK (email ~* '^[^@]+@[^@]+\.[^@]+$')
);

COMMENT ON TABLE  auth.users                    IS 'Tenant-scoped user accounts — one row per user per covered entity';
COMMENT ON COLUMN auth.users.hashed_password    IS 'bcrypt hash (rounds=12) — plaintext is never stored or logged';
COMMENT ON COLUMN auth.users.role               IS 'Cumulative RBAC role: analyst < senior_analyst < auditor < admin < system';
COMMENT ON COLUMN auth.users.tenant_id          IS 'FK to ref.covered_entities.ce_id — enforces strict tenant isolation';
COMMENT ON COLUMN auth.users.failed_login_count IS 'Reset to 0 on successful login; triggers lockout at 10 consecutive failures';

-- Indexes
CREATE INDEX idx_users_email       ON auth.users(email);
CREATE INDEX idx_users_tenant_id   ON auth.users(tenant_id);
CREATE INDEX idx_users_role        ON auth.users(role);
CREATE INDEX idx_users_is_active   ON auth.users(is_active) WHERE is_active = TRUE;


-- =============================================================================
-- auth.user_sessions
-- Purpose : Persistent audit log of issued refresh tokens.
--           The in-memory session store (_InMemoryStore) is the fast lookup path;
--           this table is the durable record for compliance and forensics.
-- Notes   : Rows are NEVER deleted — set revoked_at on logout / rotation.
-- =============================================================================
CREATE TABLE auth.user_sessions (
    session_id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Token identity
    jti                 VARCHAR(64)     NOT NULL UNIQUE,     -- JWT ID from refresh token
    user_id             UUID            NOT NULL REFERENCES auth.users(user_id),
    tenant_id           UUID            NOT NULL REFERENCES ref.covered_entities(ce_id),

    -- Provenance
    ip_address          INET,
    user_agent          TEXT,

    -- Lifecycle
    issued_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ     NOT NULL,
    revoked_at          TIMESTAMPTZ,
    revoke_reason       VARCHAR(50),    -- 'logout', 'rotation', 'admin_revoke', 'expired'

    CONSTRAINT ck_revoke_reason CHECK (
        revoke_reason IS NULL OR revoke_reason IN (
            'logout', 'rotation', 'admin_revoke', 'expired', 'security_event'
        )
    )
);

COMMENT ON TABLE  auth.user_sessions         IS 'Durable audit log of refresh token issuance and revocation';
COMMENT ON COLUMN auth.user_sessions.jti     IS 'JWT ID — matches payload.jti in the refresh token';
COMMENT ON COLUMN auth.user_sessions.revoked_at IS 'NULL = session still valid (pending expiry); NOT NULL = revoked';

CREATE INDEX idx_sessions_jti       ON auth.user_sessions(jti);
CREATE INDEX idx_sessions_user_id   ON auth.user_sessions(user_id);
CREATE INDEX idx_sessions_tenant_id ON auth.user_sessions(tenant_id);
CREATE INDEX idx_sessions_active    ON auth.user_sessions(user_id)
    WHERE revoked_at IS NULL;


-- =============================================================================
-- auth.password_history
-- Purpose : Tracks the last N hashed passwords per user to prevent reuse.
--           Policy: cannot reuse any of the last 12 passwords.
-- =============================================================================
CREATE TABLE auth.password_history (
    history_id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID            NOT NULL REFERENCES auth.users(user_id) ON DELETE CASCADE,
    hashed_password     TEXT            NOT NULL,
    set_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE auth.password_history IS 'Last 12 password hashes per user — enforces no-reuse policy';

CREATE INDEX idx_pwd_history_user ON auth.password_history(user_id, set_at DESC);
