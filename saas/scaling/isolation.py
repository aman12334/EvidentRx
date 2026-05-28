"""
Workload isolation — per-tenant resource budgets and circuit breakers.

Prevents a single tenant's workload from degrading service for others
by enforcing per-tenant CPU/memory/concurrency budgets and tripping
a circuit breaker when a tenant consistently exceeds its allocation.

Workload tiers map to resource ceilings
─────────────────────────────────────────
  BACKGROUND   — bulk import, scheduled reports, trend backfill
  STANDARD     — investigation execution, API requests
  ELEVATED     — escalation workflows, SLA-bound cases
  CRITICAL     — regulatory deadline cases, legal-hold queries

Circuit breaker states
──────────────────────
  CLOSED   — normal operation; requests flow through
  OPEN     — budget exhausted; requests are rejected
  HALF_OPEN — one probe allowed; closes if it succeeds
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime    import datetime, timedelta, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.saas.scaling.isolation")


class WorkloadTier(str, Enum):
    BACKGROUND = "background"
    STANDARD   = "standard"
    ELEVATED   = "elevated"
    CRITICAL   = "critical"


class CircuitState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


@dataclass
class ResourceBudget:
    """Per-tenant resource ceiling for a workload tier."""
    max_concurrent:    int    = 10     # max parallel executions
    max_queue_depth:   int    = 100    # max queued items
    cpu_weight:        float  = 1.0    # relative CPU share (1.0 = normal)
    memory_mb:         int    = 512    # max memory per execution
    timeout_seconds:   int    = 300    # max execution duration


# Defaults per tier
_TIER_BUDGETS: dict[WorkloadTier, ResourceBudget] = {
    WorkloadTier.BACKGROUND: ResourceBudget(max_concurrent=2, max_queue_depth=500, cpu_weight=0.3, timeout_seconds=3600),
    WorkloadTier.STANDARD:   ResourceBudget(max_concurrent=10, max_queue_depth=100, cpu_weight=1.0, timeout_seconds=300),
    WorkloadTier.ELEVATED:   ResourceBudget(max_concurrent=20, max_queue_depth=50,  cpu_weight=2.0, timeout_seconds=120),
    WorkloadTier.CRITICAL:   ResourceBudget(max_concurrent=5,  max_queue_depth=20,  cpu_weight=3.0, timeout_seconds=60),
}


@dataclass
class TenantWorkloadState:
    """Live resource usage and circuit breaker state for one tenant."""
    tenant_id:       str
    tier:            WorkloadTier
    budget:          ResourceBudget
    circuit:         CircuitState   = CircuitState.CLOSED
    active_count:    int            = 0
    queue_depth:     int            = 0
    trip_count:      int            = 0
    last_tripped_at: Optional[float] = None   # monotonic
    half_open_at:    Optional[float] = None
    _HALF_OPEN_SECS: int            = 30      # probe window after open

    @property
    def is_accepting(self) -> bool:
        if self.circuit == CircuitState.CLOSED:
            return (
                self.active_count < self.budget.max_concurrent
                and self.queue_depth < self.budget.max_queue_depth
            )
        if self.circuit == CircuitState.HALF_OPEN:
            return self.active_count == 0
        return False   # OPEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":    self.tenant_id,
            "tier":         self.tier.value,
            "circuit":      self.circuit.value,
            "active_count": self.active_count,
            "queue_depth":  self.queue_depth,
            "trip_count":   self.trip_count,
            "max_concurrent": self.budget.max_concurrent,
            "max_queue_depth":self.budget.max_queue_depth,
        }


class WorkloadIsolationManager:
    """
    Enforces per-tenant workload budgets and circuit breakers.

    Usage
    ─────
    1. Call acquire(tenant_id, tier) before starting work.
       Returns True if accepted; False if budget exceeded or circuit OPEN.
    2. Call release(tenant_id, tier, success) when work finishes.
       Pass success=False to record a failure (contributes to tripping).
    """

    def __init__(
        self,
        failure_threshold: int   = 5,    # failures before OPEN
        half_open_secs:    int   = 30,
    ) -> None:
        self._states:            dict[tuple[str, str], TenantWorkloadState] = {}
        self._failure_counts:    dict[tuple[str, str], int] = {}
        self._failure_threshold  = failure_threshold
        self._half_open_secs     = half_open_secs
        self._custom_budgets:    dict[tuple[str, str], ResourceBudget] = {}

    def set_budget(
        self,
        tenant_id: str,
        tier:      WorkloadTier,
        budget:    ResourceBudget,
    ) -> None:
        self._custom_budgets[(tenant_id, tier.value)] = budget
        state = self._get_state(tenant_id, tier)
        state.budget = budget
        log.info(
            "WorkloadIsolationManager: custom budget set for tenant %s tier %s",
            tenant_id[:8], tier.value,
        )

    def acquire(self, tenant_id: str, tier: WorkloadTier) -> bool:
        state = self._get_state(tenant_id, tier)
        self._tick_circuit(state)

        if not state.is_accepting:
            log.debug(
                "WorkloadIsolationManager: REJECTED tenant %s tier %s (circuit=%s)",
                tenant_id[:8], tier.value, state.circuit.value,
            )
            return False

        state.active_count += 1
        return True

    def release(
        self,
        tenant_id: str,
        tier:      WorkloadTier,
        success:   bool = True,
    ) -> None:
        state = self._get_state(tenant_id, tier)
        state.active_count = max(0, state.active_count - 1)

        key = (tenant_id, tier.value)
        if not success:
            self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
            if self._failure_counts[key] >= self._failure_threshold:
                self._trip(state)
        else:
            # Success probe in HALF_OPEN → close
            if state.circuit == CircuitState.HALF_OPEN:
                state.circuit = CircuitState.CLOSED
                self._failure_counts[key] = 0
                log.info(
                    "WorkloadIsolationManager: circuit CLOSED for tenant %s tier %s",
                    tenant_id[:8], tier.value,
                )

    def queue_depth_change(
        self,
        tenant_id: str,
        tier:      WorkloadTier,
        delta:     int,
    ) -> None:
        state = self._get_state(tenant_id, tier)
        state.queue_depth = max(0, state.queue_depth + delta)

    def get_state(
        self,
        tenant_id: str,
        tier:      WorkloadTier,
    ) -> TenantWorkloadState:
        return self._get_state(tenant_id, tier)

    def list_open_circuits(self) -> list[TenantWorkloadState]:
        return [
            s for s in self._states.values()
            if s.circuit == CircuitState.OPEN
        ]

    def reset_circuit(self, tenant_id: str, tier: WorkloadTier) -> None:
        """Manually close a circuit (platform_admin action)."""
        state = self._get_state(tenant_id, tier)
        state.circuit = CircuitState.CLOSED
        self._failure_counts[(tenant_id, tier.value)] = 0
        log.info(
            "WorkloadIsolationManager: circuit manually CLOSED for tenant %s tier %s",
            tenant_id[:8], tier.value,
        )

    def platform_summary(self) -> dict[str, Any]:
        open_circuits = self.list_open_circuits()
        return {
            "total_tenants_tracked": len(self._states),
            "open_circuits":         len(open_circuits),
            "open_details":          [s.to_dict() for s in open_circuits],
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_state(self, tenant_id: str, tier: WorkloadTier) -> TenantWorkloadState:
        key = (tenant_id, tier.value)
        if key not in self._states:
            budget = self._custom_budgets.get(key) or _TIER_BUDGETS[tier]
            self._states[key] = TenantWorkloadState(
                tenant_id = tenant_id,
                tier      = tier,
                budget    = budget,
            )
        return self._states[key]

    def _trip(self, state: TenantWorkloadState) -> None:
        if state.circuit != CircuitState.OPEN:
            state.circuit         = CircuitState.OPEN
            state.trip_count     += 1
            state.last_tripped_at = time.monotonic()
            log.warning(
                "WorkloadIsolationManager: circuit OPEN for tenant %s tier %s (trip #%d)",
                state.tenant_id[:8], state.tier.value, state.trip_count,
            )

    def _tick_circuit(self, state: TenantWorkloadState) -> None:
        """Transition OPEN → HALF_OPEN after the cooldown window."""
        if state.circuit != CircuitState.OPEN:
            return
        if state.last_tripped_at is None:
            return
        elapsed = time.monotonic() - state.last_tripped_at
        if elapsed >= self._half_open_secs:
            state.circuit      = CircuitState.HALF_OPEN
            state.half_open_at = time.monotonic()
            log.info(
                "WorkloadIsolationManager: circuit HALF_OPEN for tenant %s tier %s",
                state.tenant_id[:8], state.tier.value,
            )


# ── Singleton ──────────────────────────────────────────────────────────────────

_manager: Optional[WorkloadIsolationManager] = None


def get_isolation_manager(
    failure_threshold: int = 5,
    half_open_secs:    int = 30,
) -> WorkloadIsolationManager:
    global _manager
    if _manager is None:
        _manager = WorkloadIsolationManager(
            failure_threshold = failure_threshold,
            half_open_secs    = half_open_secs,
        )
    return _manager
