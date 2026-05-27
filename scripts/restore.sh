#!/usr/bin/env bash
# ==============================================================================
# restore.sh — EvidentRx database restore from S3 backup
#
# Downloads a backup from S3 and restores it into the target PostgreSQL
# database. Intended for disaster recovery — requires human approval
# before executing in production.
#
# Usage:
#   restore.sh --backup s3://bucket/path/to/backup.dump.gz \
#              --target-db postgresql://user:pass@host:5432/db \
#              [--force]   # skip confirmation prompt
#
# IMPORTANT: This script DROPS and RECREATES the target database.
#            Only run on an empty or disposable database unless you
#            know exactly what you are doing.
# ==============================================================================
set -euo pipefail

BACKUP_S3=""
TARGET_DB=""
FORCE=false
AWS_REGION="${AWS_REGION:-us-east-1}"
RESTORE_DIR="${RESTORE_DIR:-/tmp/evidentrx-restore}"

log()  { echo "[restore] $(date -u +%Y-%m-%dT%H:%M:%SZ)  $*"; }
die()  { log "FATAL: $*" >&2; exit 1; }
warn() { echo "[restore] WARNING: $*" >&2; }

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup)     BACKUP_S3="$2";   shift 2 ;;
        --target-db)  TARGET_DB="$2";   shift 2 ;;
        --force)      FORCE=true;       shift   ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ -n "${BACKUP_S3}" ]]  || die "--backup s3://... is required"
[[ -n "${TARGET_DB}" ]]  || die "--target-db postgresql://... is required"

# ── Safety confirmation ───────────────────────────────────────────────────────
if [[ "${FORCE}" != "true" ]]; then
    warn "This will OVERWRITE the target database: ${TARGET_DB}"
    warn "Backup source: ${BACKUP_S3}"
    read -r -p "Type 'yes I understand' to proceed: " CONFIRM
    [[ "${CONFIRM}" == "yes I understand" ]] || die "Restore cancelled."
fi

mkdir -p "${RESTORE_DIR}"
LOCAL_FILE="${RESTORE_DIR}/restore-$(date +%s).dump.gz"

# ── Download from S3 ──────────────────────────────────────────────────────────
log "Downloading backup from ${BACKUP_S3}..."
aws s3 cp "${BACKUP_S3}" "${LOCAL_FILE}" --region "${AWS_REGION}"
log "Download complete: $(du -sh "${LOCAL_FILE}" | cut -f1)"

# ── Decompress ────────────────────────────────────────────────────────────────
DUMP_FILE="${LOCAL_FILE%.gz}"
gunzip -c "${LOCAL_FILE}" > "${DUMP_FILE}"

# Normalize URL (strip asyncpg prefix if present)
DB_URL="${TARGET_DB/postgresql+asyncpg:\/\//postgresql://}"

# ── Restore ───────────────────────────────────────────────────────────────────
log "Starting pg_restore into ${DB_URL}..."
pg_restore "${DB_URL}" \
    --format=custom \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --single-transaction \
    "${DUMP_FILE}"

log "✅ Restore complete."

# ── Cleanup ───────────────────────────────────────────────────────────────────
rm -f "${LOCAL_FILE}" "${DUMP_FILE}"
log "Local files cleaned up."
