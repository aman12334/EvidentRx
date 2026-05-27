#!/usr/bin/env bash
# ==============================================================================
# backup.sh — EvidentRx database backup
#
# Creates a compressed pg_dump of the evidentrx database and uploads it
# to an S3-compatible bucket. Enforces 7-year retention tagging per
# HRSA 340B record-keeping requirements.
#
# Usage:
#   backup.sh [--env staging|production] [--output /path/to/dir]
#
# Required env:
#   DATABASE_URL    — PostgreSQL connection string
#   BACKUP_BUCKET   — S3 bucket name (e.g. evidentrx-backups-prod)
#   ENVIRONMENT     — staging | production
#
# Optional env:
#   BACKUP_PREFIX   — S3 key prefix (default: "db-backups")
#   AWS_REGION      — AWS region (default: us-east-1)
# ==============================================================================
set -euo pipefail

ENVIRONMENT="${ENVIRONMENT:-staging}"
BACKUP_BUCKET="${BACKUP_BUCKET:-}"
BACKUP_PREFIX="${BACKUP_PREFIX:-db-backups}"
AWS_REGION="${AWS_REGION:-us-east-1}"
OUTPUT_DIR="${1:-/tmp/evidentrx-backups}"

log()  { echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ)  $*"; }
die()  { log "FATAL: $*" >&2; exit 1; }

[[ -n "${DATABASE_URL:-}" ]] || die "DATABASE_URL is required"
[[ -n "${BACKUP_BUCKET:-}" ]] || die "BACKUP_BUCKET is required"

mkdir -p "${OUTPUT_DIR}"

TIMESTAMP=$(date -u +%Y%m%d-%H%M%S)
BACKUP_FILE="${OUTPUT_DIR}/evidentrx-${ENVIRONMENT}-${TIMESTAMP}.dump.gz"
S3_KEY="${BACKUP_PREFIX}/${ENVIRONMENT}/${TIMESTAMP}/evidentrx.dump.gz"

log "Starting backup for environment: ${ENVIRONMENT}"
log "Output: ${BACKUP_FILE}"

# ── Extract connection params ─────────────────────────────────────────────────
# Handles both asyncpg and psycopg2 URL schemes
DB_URL="${DATABASE_URL/postgresql+asyncpg:\/\//postgresql://}"

# ── pg_dump ───────────────────────────────────────────────────────────────────
log "Running pg_dump (custom format, compressed)..."
pg_dump "${DB_URL}" \
    --format=custom \
    --compress=9 \
    --no-owner \
    --no-privileges \
    --file="${BACKUP_FILE%.gz}"   # pg_dump custom format is already binary

# Compress separately for S3 transfer efficiency
gzip -9 "${BACKUP_FILE%.gz}"

BACKUP_SIZE=$(du -sh "${BACKUP_FILE}" | cut -f1)
log "Backup complete: ${BACKUP_SIZE}"

# ── Upload to S3 ──────────────────────────────────────────────────────────────
log "Uploading to s3://${BACKUP_BUCKET}/${S3_KEY}..."
aws s3 cp "${BACKUP_FILE}" "s3://${BACKUP_BUCKET}/${S3_KEY}" \
    --region "${AWS_REGION}" \
    --storage-class STANDARD_IA \
    --metadata "environment=${ENVIRONMENT},timestamp=${TIMESTAMP},retention=7years" \
    --tagging "Environment=${ENVIRONMENT}&Retention=7years&Service=evidentrx"

log "Upload complete: s3://${BACKUP_BUCKET}/${S3_KEY}"

# ── Set S3 lifecycle tag for 7-year retention ─────────────────────────────────
# (Lifecycle rules should be configured on the bucket; this tag acts as a gate)
log "Tagging backup with 340B retention policy..."
aws s3api put-object-tagging \
    --bucket "${BACKUP_BUCKET}" \
    --key "${S3_KEY}" \
    --tagging '{
        "TagSet": [
            {"Key": "HIPAARetention",  "Value": "required"},
            {"Key": "RetentionYears",  "Value": "7"},
            {"Key": "HRSACompliant",   "Value": "true"},
            {"Key": "Environment",     "Value": "'"${ENVIRONMENT}"'"},
            {"Key": "BackupTimestamp", "Value": "'"${TIMESTAMP}"'"}
        ]
    }' \
    --region "${AWS_REGION}"

log "✅ Backup successful: s3://${BACKUP_BUCKET}/${S3_KEY} (${BACKUP_SIZE})"

# ── Cleanup local file ────────────────────────────────────────────────────────
rm -f "${BACKUP_FILE}"
log "Local backup file cleaned up."
