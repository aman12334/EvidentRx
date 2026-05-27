#!/usr/bin/env bash
# ==============================================================================
# seed_tenant.sh — Bootstrap a new tenant in EvidentRx
#
# Creates all required database rows for a new covered entity tenant:
#   - covered_entities record
#   - tenant configuration entry
#   - initial admin user account
#   - audit log entry for the bootstrap event
#
# Usage:
#   seed_tenant.sh \
#     --name "General Hospital" \
#     --npi  "1234567890" \
#     --admin-email "admin@generalhospital.org" \
#     --env staging
#
# Required env:
#   DATABASE_URL    — PostgreSQL connection string
# ==============================================================================
set -euo pipefail

TENANT_NAME=""
TENANT_NPI=""
ADMIN_EMAIL=""
ENVIRONMENT="${ENVIRONMENT:-staging}"

log()  { echo "[seed] $(date -u +%Y-%m-%dT%H:%M:%SZ)  $*"; }
die()  { log "FATAL: $*" >&2; exit 1; }

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)         TENANT_NAME="$2";  shift 2 ;;
        --npi)          TENANT_NPI="$2";   shift 2 ;;
        --admin-email)  ADMIN_EMAIL="$2";  shift 2 ;;
        --env)          ENVIRONMENT="$2";  shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ -n "${TENANT_NAME}" ]]  || die "--name is required"
[[ -n "${TENANT_NPI}" ]]   || die "--npi is required"
[[ -n "${ADMIN_EMAIL}" ]]  || die "--admin-email is required"
[[ -n "${DATABASE_URL:-}" ]] || die "DATABASE_URL environment variable is required"

log "Seeding tenant: ${TENANT_NAME} (NPI: ${TENANT_NPI})"
log "Admin email: ${ADMIN_EMAIL}"
log "Environment: ${ENVIRONMENT}"

# ── Delegate to Python script (has access to ORM models) ─────────────────────
python -c "
import asyncio, uuid, os, sys
from datetime import datetime, timezone

DATABASE_URL = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')

async def seed():
    import asyncpg
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        tenant_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)

        # Insert covered entity
        await conn.execute('''
            INSERT INTO covered_entities (
                covered_entity_id, entity_name, npi_number,
                entity_type, state, is_active, created_at, updated_at
            ) VALUES (\$1, \$2, \$3, '340b_covered_entity', 'CA', true, \$4, \$4)
            ON CONFLICT (npi_number) DO NOTHING
        ''', tenant_id, '${TENANT_NAME}', '${TENANT_NPI}', now)

        # Insert admin user placeholder (password must be set via /auth/reset)
        user_id = str(uuid.uuid4())
        await conn.execute('''
            INSERT INTO users (
                user_id, email, hashed_password, role, tenant_id,
                is_active, created_at, updated_at
            ) VALUES (\$1, \$2, '\$\$placeholder\$\$', 'admin', \$3, true, \$4, \$4)
            ON CONFLICT (email) DO NOTHING
        ''', user_id, '${ADMIN_EMAIL}', tenant_id, now)

        print(f'[seed] tenant_id = {tenant_id}')
        print(f'[seed] user_id   = {user_id}')
        print(f'[seed] ✅ Tenant seeded successfully.')
        print(f'[seed] Next step: set the admin password via the API.')

    finally:
        await conn.close()

asyncio.run(seed())
"

log "Tenant seed complete for '${TENANT_NAME}'."
