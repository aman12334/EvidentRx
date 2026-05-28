#!/usr/bin/env bash
# ==============================================================================
# EvidentRx container entrypoint
#
# Usage: entrypoint.sh [api|worker|beat|migrate|seed]
#
# Runs pre-flight checks then delegates to the appropriate process.
# Exits non-zero on any pre-flight failure so Kubernetes can detect
# unhealthy pods during init and restart them rather than accepting traffic.
# ==============================================================================
set -euo pipefail

COMMAND="${1:-api}"

# ── Helpers ───────────────────────────────────────────────────────────────────

log() { echo "[entrypoint] $(date -u +%Y-%m-%dT%H:%M:%SZ)  $*"; }
die() { log "FATAL: $*" >&2; exit 1; }

# ── Validate required env vars ────────────────────────────────────────────────

require_env() {
    local var="$1"
    [[ -n "${!var:-}" ]] || die "Required environment variable $var is not set."
}

require_env DATABASE_URL
require_env JWT_SECRET_KEY
require_env ENVIRONMENT

# Block CHANGE_ME placeholder secrets in production
if [[ "${ENVIRONMENT}" == "production" ]]; then
    [[ "${JWT_SECRET_KEY}" != "CHANGE_ME"* ]] || die "JWT_SECRET_KEY is a placeholder — set a real secret before deploying to production."
    [[ "${DATABASE_URL}" != *"localhost"* ]]   || die "DATABASE_URL points to localhost in production — this is not allowed."
fi

log "Environment: ${ENVIRONMENT}"
log "Command:     ${COMMAND}"

# ── Wait for dependencies ─────────────────────────────────────────────────────

wait_for_postgres() {
    log "Waiting for PostgreSQL..."
    local retries=30
    until python -c "
import asyncio, asyncpg, os, sys
async def check():
    try:
        conn = await asyncpg.connect(os.environ['DATABASE_URL'], timeout=3)
        await conn.close()
    except Exception as e:
        sys.exit(1)
asyncio.run(check())
" 2>/dev/null; do
        retries=$((retries - 1))
        [[ $retries -gt 0 ]] || die "PostgreSQL did not become available in time."
        log "  PostgreSQL not ready — retrying ($retries left)..."
        sleep 2
    done
    log "PostgreSQL is ready."
}

wait_for_redis() {
    if [[ -z "${CELERY_BROKER_URL:-}" ]]; then
        log "CELERY_BROKER_URL not set — skipping Redis check."
        return
    fi
    log "Waiting for Redis..."
    local retries=20
    until python -c "
import redis, os, sys
try:
    r = redis.from_url(os.environ['CELERY_BROKER_URL'], socket_timeout=3)
    r.ping()
except Exception:
    sys.exit(1)
" 2>/dev/null; do
        retries=$((retries - 1))
        [[ $retries -gt 0 ]] || die "Redis did not become available in time."
        log "  Redis not ready — retrying ($retries left)..."
        sleep 2
    done
    log "Redis is ready."
}

# ── Run database migrations (always before api/worker start) ──────────────────

run_migrations() {
    log "Running Alembic migrations..."
    alembic upgrade head
    log "Migrations complete."
}

# ── Process dispatch ──────────────────────────────────────────────────────────

case "${COMMAND}" in

  api)
    wait_for_postgres
    run_migrations
    log "Starting uvicorn API server..."
    exec uvicorn api.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --workers "${UVICORN_WORKERS:-2}" \
        --loop uvloop \
        --http h11 \
        --log-level "${LOG_LEVEL:-info}" \
        --no-access-log   # structured logging handles access logs via middleware
    ;;

  worker)
    wait_for_postgres
    wait_for_redis
    log "Starting Celery worker..."
    exec celery -A tasks.queue.celery_app worker \
        --loglevel="${LOG_LEVEL:-info}" \
        --concurrency="${CELERY_CONCURRENCY:-4}" \
        --queues=default,monitoring,agents,archival \
        --hostname="worker@%h"
    ;;

  beat)
    wait_for_redis
    log "Starting Celery beat scheduler..."
    exec celery -A tasks.queue.celery_app beat \
        --loglevel="${LOG_LEVEL:-info}"
    ;;

  migrate)
    wait_for_postgres
    run_migrations
    ;;

  seed)
    wait_for_postgres
    log "Running tenant seed script..."
    exec python scripts/seed_tenant.py
    ;;

  seed-demo)
    wait_for_postgres
    run_migrations
    log "Running demo seed (compliance rules + demo data)..."
    python -c "
import asyncio, asyncpg, os
async def run():
    url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://').replace('postgresql+psycopg2://','postgresql://')
    conn = await asyncpg.connect(url)
    with open('database/seeds/compliance_rules.sql') as f:
        await conn.execute(f.read())
    await conn.close()
asyncio.run(run())
"
    python -m database.seeds.demo_data
    log "Demo seed complete."
    ;;

  *)
    die "Unknown command '${COMMAND}'. Valid options: api | worker | beat | migrate | seed | seed-demo"
    ;;

esac
