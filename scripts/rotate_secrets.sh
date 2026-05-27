#!/usr/bin/env bash
# ==============================================================================
# rotate_secrets.sh — Rotate JWT signing key and update all dependents
#
# Rotation procedure:
#   1. Generate a new cryptographically random JWT secret key
#   2. Store in AWS Secrets Manager (with version staging)
#   3. Update the Kubernetes secret in the target namespace
#   4. Trigger a rolling restart of API pods to pick up the new key
#   5. Wait for rollout to complete before returning
#
# IMPORTANT: After rotation all existing JWTs signed with the old key become
# invalid — users will need to log in again. Plan this during a maintenance
# window or implement dual-key verification during the transition period.
#
# Usage:
#   rotate_secrets.sh [--namespace evidentrx] [--secret-name evidentrx-secrets]
#
# Required env:
#   AWS_REGION
#   SECRETS_MANAGER_ARN   — ARN of the secret in AWS Secrets Manager
# ==============================================================================
set -euo pipefail

NAMESPACE="${NAMESPACE:-evidentrx}"
K8S_SECRET_NAME="${K8S_SECRET_NAME:-evidentrx-secrets}"
AWS_REGION="${AWS_REGION:-us-east-1}"
SECRETS_MANAGER_ARN="${SECRETS_MANAGER_ARN:-}"

log()  { echo "[rotate] $(date -u +%Y-%m-%dT%H:%M:%SZ)  $*"; }
die()  { log "FATAL: $*" >&2; exit 1; }
warn() { echo "[rotate] WARNING: $*" >&2; }

[[ -n "${SECRETS_MANAGER_ARN:-}" ]] || die "SECRETS_MANAGER_ARN is required"

# ── Generate new secret ───────────────────────────────────────────────────────
log "Generating new JWT secret key (64 bytes, base64url-encoded)..."
NEW_JWT_SECRET=$(python3 -c "
import secrets, base64
raw = secrets.token_bytes(64)
print(base64.urlsafe_b64encode(raw).decode().rstrip('='))
")

log "New secret generated (length: ${#NEW_JWT_SECRET} chars)"

# ── Update AWS Secrets Manager ────────────────────────────────────────────────
log "Updating AWS Secrets Manager: ${SECRETS_MANAGER_ARN}..."

# Fetch current secret value to update only the JWT key
CURRENT_SECRET=$(aws secretsmanager get-secret-value \
    --secret-id "${SECRETS_MANAGER_ARN}" \
    --region "${AWS_REGION}" \
    --query SecretString \
    --output text)

UPDATED_SECRET=$(echo "${CURRENT_SECRET}" | python3 -c "
import json, sys
secret = json.load(sys.stdin)
secret['jwt_secret_key'] = '${NEW_JWT_SECRET}'
print(json.dumps(secret))
")

aws secretsmanager put-secret-value \
    --secret-id "${SECRETS_MANAGER_ARN}" \
    --secret-string "${UPDATED_SECRET}" \
    --region "${AWS_REGION}"

log "AWS Secrets Manager updated."

# ── Update Kubernetes secret ──────────────────────────────────────────────────
log "Patching Kubernetes secret ${K8S_SECRET_NAME} in namespace ${NAMESPACE}..."

NEW_JWT_SECRET_B64=$(echo -n "${NEW_JWT_SECRET}" | base64)

kubectl patch secret "${K8S_SECRET_NAME}" \
    -n "${NAMESPACE}" \
    --type='json' \
    -p="[{\"op\": \"replace\", \"path\": \"/data/jwt-secret-key\", \"value\": \"${NEW_JWT_SECRET_B64}\"}]"

log "Kubernetes secret patched."

# ── Rolling restart API pods ──────────────────────────────────────────────────
warn "Triggering rolling restart — existing sessions will be invalidated."

kubectl rollout restart deployment/evidentrx-api -n "${NAMESPACE}"

log "Waiting for rollout to complete..."
kubectl rollout status deployment/evidentrx-api \
    -n "${NAMESPACE}" \
    --timeout=3m

log "✅ Secret rotation complete. New JWT key is live."
log "   Users with existing tokens will need to re-authenticate."
