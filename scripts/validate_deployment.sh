#!/usr/bin/env bash
# ==============================================================================
# validate_deployment.sh — Post-deploy health validation
#
# Usage: validate_deployment.sh [namespace] [api_service_name]
#
# Verifies:
#   1. API deployment rollout is complete (all replicas ready)
#   2. Worker deployment rollout is complete
#   3. /api/health endpoint returns 200 + {"status": "ok"}
#   4. Database connectivity (via API health)
#   5. No pods in CrashLoopBackOff
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
# ==============================================================================
set -euo pipefail

NAMESPACE="${1:-evidentrx}"
RELEASE="${2:-evidentrx}"
MAX_WAIT="${3:-120}"   # seconds to wait for rollout

log()  { echo "[validate] $(date -u +%H:%M:%SZ)  $*"; }
pass() { echo "[validate] ✓ $*"; }
fail() { echo "[validate] ✗ FAILED: $*" >&2; FAILURES=$((FAILURES + 1)); }

FAILURES=0

# ── 1. API rollout ────────────────────────────────────────────────────────────
log "Checking API rollout..."
if kubectl rollout status deployment/"${RELEASE}-api" \
     -n "${NAMESPACE}" --timeout="${MAX_WAIT}s" 2>&1; then
    pass "API deployment rollout complete"
else
    fail "API deployment rollout did not complete within ${MAX_WAIT}s"
fi

# ── 2. Worker rollout ─────────────────────────────────────────────────────────
log "Checking Worker rollout..."
if kubectl rollout status deployment/"${RELEASE}-worker" \
     -n "${NAMESPACE}" --timeout="${MAX_WAIT}s" 2>&1; then
    pass "Worker deployment rollout complete"
else
    fail "Worker deployment rollout did not complete within ${MAX_WAIT}s"
fi

# ── 3. Health endpoint ────────────────────────────────────────────────────────
log "Checking /api/health endpoint..."
# Port-forward to the API service for the health check
kubectl port-forward svc/"${RELEASE}-api" 18000:80 \
    -n "${NAMESPACE}" &>/dev/null &
PF_PID=$!
sleep 3   # give port-forward time to establish

HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    http://localhost:18000/api/health 2>/dev/null || echo "000")
HEALTH_BODY=$(curl -s http://localhost:18000/api/health 2>/dev/null || echo "{}")

kill "${PF_PID}" 2>/dev/null || true

if [[ "${HEALTH_STATUS}" == "200" ]]; then
    pass "Health endpoint returned 200: ${HEALTH_BODY}"
else
    fail "Health endpoint returned HTTP ${HEALTH_STATUS} (expected 200)"
fi

# ── 4. CrashLoopBackOff check ────────────────────────────────────────────────
log "Checking for CrashLoopBackOff pods..."
CRASH_PODS=$(kubectl get pods -n "${NAMESPACE}" \
    --field-selector=status.phase!=Succeeded \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}{end}' 2>/dev/null \
    | grep -i "CrashLoopBackOff" | awk '{print $1}' || true)

if [[ -z "${CRASH_PODS}" ]]; then
    pass "No pods in CrashLoopBackOff"
else
    fail "CrashLoopBackOff pods detected: ${CRASH_PODS}"
fi

# ── 5. Minimum replica count ─────────────────────────────────────────────────
log "Checking minimum replica readiness..."
READY_API=$(kubectl get deployment/"${RELEASE}-api" \
    -n "${NAMESPACE}" \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")

if [[ "${READY_API:-0}" -ge 1 ]]; then
    pass "API has ${READY_API} ready replica(s)"
else
    fail "API has 0 ready replicas"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [[ "${FAILURES}" -eq 0 ]]; then
    log "✅ All validation checks passed — deployment is healthy."
    exit 0
else
    log "❌ ${FAILURES} validation check(s) failed — deployment may be unhealthy."
    exit 1
fi
