"""
Horizontal scaling orchestration.

Monitors platform-wide resource utilisation and recommends (or
automatically triggers) scaling actions for worker pools. The
HorizontalScalingManager integrates with the WorkloadIsolationManager
to detect overloaded partitions and the TenantQueuePartitioner to
redistribute tenants when necessary.

Scaling actions are expressed as recommendations — the actual
infrastructure resize (Kubernetes HPA, ECS service update, etc.)
is performed by an injected actuator callable. This keeps the
orchestration logic platform-agnostic.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.scaling.orchestration")


class ScaleDirection(str, Enum):
    UP   = "up"
    DOWN = "down"


class ScalingTrigger(str, Enum):
    QUEUE_DEPTH      = "queue_depth"
    CIRCUIT_OPEN     = "circuit_open"
    LATENCY_SPIKE    = "latency_spike"
    MANUAL           = "manual"
    SCHEDULE         = "schedule"


@dataclass
class ScalingConfig:
    """
    Autoscaling policy for a worker pool.

    Thresholds are checked every ``check_interval_seconds`` seconds.
    Scale-up fires when avg_queue_depth > scale_up_threshold.
    Scale-down fires when avg_queue_depth < scale_down_threshold
    and the current replica count exceeds min_replicas.
    """
    pool_name:             str
    min_replicas:          int    = 1
    max_replicas:          int    = 10
    scale_up_threshold:    float  = 0.75   # queue utilisation fraction
    scale_down_threshold:  float  = 0.25
    scale_up_step:         int    = 2      # replicas to add per step
    scale_down_step:       int    = 1      # replicas to remove per step
    cooldown_seconds:      int    = 120    # min seconds between scale actions
    check_interval_seconds: int   = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_name":            self.pool_name,
            "min_replicas":         self.min_replicas,
            "max_replicas":         self.max_replicas,
            "scale_up_threshold":   self.scale_up_threshold,
            "scale_down_threshold": self.scale_down_threshold,
            "cooldown_seconds":     self.cooldown_seconds,
        }


@dataclass
class ScalingEvent:
    """Record of a scaling decision."""
    event_id:       str
    pool_name:      str
    direction:      ScaleDirection
    trigger:        ScalingTrigger
    from_replicas:  int
    to_replicas:    int
    utilisation:    float
    reason:         str
    decided_at:     datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    applied:        bool     = False
    apply_error:    str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":      self.event_id,
            "pool_name":     self.pool_name,
            "direction":     self.direction.value,
            "trigger":       self.trigger.value,
            "from_replicas": self.from_replicas,
            "to_replicas":   self.to_replicas,
            "utilisation":   round(self.utilisation, 3),
            "reason":        self.reason,
            "decided_at":    self.decided_at.isoformat(),
            "applied":       self.applied,
        }


@dataclass
class PoolMetrics:
    """Current runtime metrics for a worker pool."""
    pool_name:       str
    current_replicas: int
    queue_depth:     int
    queue_capacity:  int
    active_workers:  int
    sampled_at:      float   = field(default_factory=time.monotonic)

    @property
    def utilisation(self) -> float:
        if self.queue_capacity == 0:
            return 0.0
        return min(1.0, self.queue_depth / self.queue_capacity)


import uuid as _uuid


class HorizontalScalingManager:
    """
    Evaluates pool metrics and issues scaling recommendations.

    The ``actuator`` callable is responsible for actually resizing the
    pool. Its signature is: async fn(pool_name, target_replicas) → None.
    If no actuator is provided, events are recorded as recommendations
    only (applied=False).
    """

    def __init__(
        self,
        actuator: Callable | None = None,
    ) -> None:
        # pool_name → ScalingConfig
        self._configs:      dict[str, ScalingConfig] = {}
        # pool_name → current_replicas
        self._replicas:     dict[str, int]            = {}
        # pool_name → last scale time (monotonic)
        self._last_scaled:  dict[str, float]          = {}
        # scaling event history
        self._events:       list[ScalingEvent]        = []
        self._actuator      = actuator

    def register_pool(self, config: ScalingConfig, initial_replicas: int = 1) -> None:
        self._configs[config.pool_name]  = config
        self._replicas[config.pool_name] = max(config.min_replicas, initial_replicas)
        log.info(
            "HorizontalScalingManager: registered pool '%s' (init=%d replicas)",
            config.pool_name, self._replicas[config.pool_name],
        )

    async def evaluate(
        self,
        metrics: list[PoolMetrics],
        trigger: ScalingTrigger = ScalingTrigger.QUEUE_DEPTH,
    ) -> list[ScalingEvent]:
        """
        Evaluate pool metrics and fire scaling actions if needed.

        Returns the list of ScalingEvents created this evaluation cycle.
        """
        events: list[ScalingEvent] = []
        now = time.monotonic()

        for m in metrics:
            cfg = self._configs.get(m.pool_name)
            if cfg is None:
                continue

            current = self._replicas.get(m.pool_name, cfg.min_replicas)
            last    = self._last_scaled.get(m.pool_name, 0.0)

            # Cooldown guard
            if now - last < cfg.cooldown_seconds:
                continue

            util    = m.utilisation
            event   = None

            if util >= cfg.scale_up_threshold and current < cfg.max_replicas:
                target = min(cfg.max_replicas, current + cfg.scale_up_step)
                event  = ScalingEvent(
                    event_id      = str(_uuid.uuid4()),
                    pool_name     = m.pool_name,
                    direction     = ScaleDirection.UP,
                    trigger       = trigger,
                    from_replicas = current,
                    to_replicas   = target,
                    utilisation   = util,
                    reason        = f"Queue utilisation {util:.0%} ≥ threshold {cfg.scale_up_threshold:.0%}",
                )
            elif util <= cfg.scale_down_threshold and current > cfg.min_replicas:
                target = max(cfg.min_replicas, current - cfg.scale_down_step)
                event  = ScalingEvent(
                    event_id      = str(_uuid.uuid4()),
                    pool_name     = m.pool_name,
                    direction     = ScaleDirection.DOWN,
                    trigger       = trigger,
                    from_replicas = current,
                    to_replicas   = target,
                    utilisation   = util,
                    reason        = f"Queue utilisation {util:.0%} ≤ threshold {cfg.scale_down_threshold:.0%}",
                )

            if event:
                await self._apply(event)
                if event.applied:
                    self._replicas[m.pool_name] = event.to_replicas
                    self._last_scaled[m.pool_name] = now
                self._events.append(event)
                events.append(event)

        return events

    async def scale_manual(
        self,
        pool_name:  str,
        replicas:   int,
        reason:     str = "manual",
    ) -> ScalingEvent:
        cfg     = self._configs.get(pool_name)
        current = self._replicas.get(pool_name, 1)
        if cfg is None:
            raise ScalingError(f"Pool '{pool_name}' not registered")

        target = max(cfg.min_replicas, min(cfg.max_replicas, replicas))
        event  = ScalingEvent(
            event_id      = str(_uuid.uuid4()),
            pool_name     = pool_name,
            direction     = ScaleDirection.UP if target >= current else ScaleDirection.DOWN,
            trigger       = ScalingTrigger.MANUAL,
            from_replicas = current,
            to_replicas   = target,
            utilisation   = 0.0,
            reason        = reason,
        )
        await self._apply(event)
        if event.applied:
            self._replicas[pool_name] = target
        self._events.append(event)
        return event

    def current_replicas(self, pool_name: str) -> int:
        return self._replicas.get(pool_name, 0)

    def event_history(
        self,
        pool_name: str | None = None,
        limit:     int           = 50,
    ) -> list[ScalingEvent]:
        events = [
            e for e in self._events
            if pool_name is None or e.pool_name == pool_name
        ]
        return events[-limit:]

    def platform_summary(self) -> dict[str, Any]:
        return {
            "pools": {
                name: {
                    "current_replicas": self._replicas.get(name, 0),
                    "config":           cfg.to_dict(),
                }
                for name, cfg in self._configs.items()
            },
            "total_events": len(self._events),
        }

    async def _apply(self, event: ScalingEvent) -> None:
        if self._actuator:
            try:
                await self._actuator(event.pool_name, event.to_replicas)
                event.applied = True
                log.info(
                    "HorizontalScalingManager: scaled '%s' %s → %d replicas",
                    event.pool_name, event.direction.value, event.to_replicas,
                )
            except Exception as exc:
                event.apply_error = str(exc)
                log.error(
                    "HorizontalScalingManager: scale apply failed for '%s': %s",
                    event.pool_name, exc,
                )
        else:
            log.info(
                "HorizontalScalingManager: recommendation (no actuator) — "
                "'%s' %s → %d replicas",
                event.pool_name, event.direction.value, event.to_replicas,
            )


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ScalingError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_manager: HorizontalScalingManager | None = None


def get_scaling_manager(
    actuator: Callable | None = None,
) -> HorizontalScalingManager:
    global _manager
    if _manager is None:
        _manager = HorizontalScalingManager(actuator=actuator)
    return _manager
