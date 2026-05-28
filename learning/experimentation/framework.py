"""
Controlled A/B experimentation framework.

Provides infrastructure for running controlled experiments on prompt
versions, workflow configurations, and model routing. Experiments are
strictly non-destructive to production traffic — they run in shadow mode
or on explicitly opted-in tenants only.

Safety constraints
──────────────────
  - No experiment may modify the active production configuration.
  - Traffic assignment is deterministic (hash-based), not random —
    the same entity always gets the same arm for the experiment lifetime.
  - A maximum of ONE experiment may be active per (tenant, slot) pair.
  - All experiments require a defined stop_at timestamp.
  - Results are read-only — they do not trigger automatic promotions.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.learning.experimentation.framework")

# Maximum experiment duration: 90 days
_MAX_EXPERIMENT_DAYS = 90
# Minimum sample before results are considered reliable
_MIN_RELIABLE_SAMPLE = 30


class ExperimentArm(str, Enum):
    CONTROL   = "control"    # current production configuration
    TREATMENT = "treatment"  # candidate configuration being tested


class ExperimentState(str, Enum):
    PENDING   = "pending"    # approved, not yet started
    RUNNING   = "running"
    PAUSED    = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class ArmConfiguration:
    """Configuration for one arm (control or treatment) of an experiment."""
    arm:               ExperimentArm
    prompt_version_id: str | None   = None
    workflow_version_id: str | None = None
    model_config:      str | None   = None
    calibration_snapshot_id: str | None = None
    description:       str             = ""


@dataclass
class ABExperiment:
    """
    A controlled A/B experiment definition.

    Associates a control arm (current production) with a treatment arm
    (candidate). Traffic assignment is deterministic — given the same
    entity_id and experiment_id, the arm is always identical.
    """
    experiment_id:    str
    tenant_id:        str
    slot:             str             # PromptSlot or WorkflowType value
    name:             str
    description:      str
    control:          ArmConfiguration
    treatment:        ArmConfiguration
    state:            ExperimentState
    created_by:       str
    approved_by:      str | None
    created_at:       datetime
    start_at:         datetime | None
    stop_at:          datetime
    traffic_fraction: float           # fraction of eligible traffic in experiment (0–1)
    success_metric:   str             # primary metric to compare (e.g. "outcome_accuracy")
    min_detectable_effect: float     # MDE for the primary metric
    run_ids:          list[str]       = field(default_factory=list)
    metadata:         dict[str, Any]  = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return datetime.now(tz=UTC) > self.stop_at

    @property
    def duration_days(self) -> float:
        return (self.stop_at - self.created_at).total_seconds() / 86400

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id":    self.experiment_id,
            "tenant_id":        self.tenant_id,
            "slot":             self.slot,
            "name":             self.name,
            "state":            self.state.value,
            "created_by":       self.created_by,
            "approved_by":      self.approved_by,
            "created_at":       self.created_at.isoformat(),
            "start_at":         self.start_at.isoformat() if self.start_at else None,
            "stop_at":          self.stop_at.isoformat(),
            "traffic_fraction": self.traffic_fraction,
            "success_metric":   self.success_metric,
            "run_count":        len(self.run_ids),
        }


class ExperimentFramework:
    """
    Creates and manages A/B experiments for the learning system.

    Enforces safety constraints:
    - One active experiment per (tenant, slot)
    - Maximum duration cap
    - Traffic fraction validation
    - Read-only results (no automatic promotion)
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        self._experiments:  dict[str, ABExperiment] = {}
        # (tenant_id, slot) → experiment_id of RUNNING experiment
        self._active_slots: dict[tuple[str, str], str] = {}
        self._db_writer     = db_writer

    # ── Create ─────────────────────────────────────────────────────────────────

    async def create_experiment(
        self,
        tenant_id:       str,
        slot:            str,
        name:            str,
        description:     str,
        control:         ArmConfiguration,
        treatment:       ArmConfiguration,
        created_by:      str,
        stop_at:         datetime,
        traffic_fraction: float          = 0.10,
        success_metric:  str             = "outcome_accuracy",
        min_detectable_effect: float     = 0.02,
        start_at:        datetime | None = None,
        metadata:        dict | None  = None,
    ) -> ABExperiment:
        """
        Define a new A/B experiment.

        The experiment starts in PENDING state — call start() to activate.
        """
        now = datetime.now(tz=UTC)

        # Validate duration cap
        duration_days = (stop_at - now).total_seconds() / 86400
        if duration_days > _MAX_EXPERIMENT_DAYS:
            raise ExperimentFrameworkError(
                f"Experiment stop_at exceeds maximum duration of {_MAX_EXPERIMENT_DAYS} days"
            )

        if not 0.0 < traffic_fraction <= 1.0:
            raise ExperimentFrameworkError(
                f"traffic_fraction must be in (0, 1], got {traffic_fraction}"
            )

        # Check for already-running experiment on this slot
        key = (tenant_id, slot)
        if key in self._active_slots:
            raise ExperimentConflictError(
                f"An active experiment already exists for slot '{slot}' "
                f"on tenant {tenant_id}"
            )

        exp = ABExperiment(
            experiment_id         = str(uuid.uuid4()),
            tenant_id             = tenant_id,
            slot                  = slot,
            name                  = name,
            description           = description,
            control               = control,
            treatment             = treatment,
            state                 = ExperimentState.PENDING,
            created_by            = created_by,
            approved_by           = None,
            created_at            = now,
            start_at              = start_at,
            stop_at               = stop_at,
            traffic_fraction      = traffic_fraction,
            success_metric        = success_metric,
            min_detectable_effect = min_detectable_effect,
            metadata              = metadata or {},
        )
        self._experiments[exp.experiment_id] = exp
        await self._persist("create", exp)
        log.info(
            "ExperimentFramework: created experiment '%s' [%s] slot=%s",
            name, exp.experiment_id[:8], slot,
        )
        return exp

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def approve(
        self,
        experiment_id: str,
        approved_by:   str,
    ) -> ABExperiment:
        exp = self._require(experiment_id, ExperimentState.PENDING)
        exp.approved_by = approved_by
        await self._persist("update", exp)
        return exp

    async def start(self, experiment_id: str) -> ABExperiment:
        """Start the experiment — it begins influencing traffic assignment."""
        exp = self._require(experiment_id, ExperimentState.PENDING)
        if exp.approved_by is None:
            raise ExperimentFrameworkError(
                f"Experiment {experiment_id[:8]} must be approved before starting"
            )
        if exp.is_expired:
            raise ExperimentFrameworkError(
                f"Experiment {experiment_id[:8]} stop_at has already passed"
            )

        key = (exp.tenant_id, exp.slot)
        if key in self._active_slots:
            raise ExperimentConflictError(
                f"Another experiment is now active for slot '{exp.slot}'"
            )

        exp.state    = ExperimentState.RUNNING
        exp.start_at = exp.start_at or datetime.now(tz=UTC)
        self._active_slots[key] = experiment_id
        await self._persist("update", exp)
        log.info("ExperimentFramework: started experiment %s", experiment_id[:8])
        return exp

    async def pause(self, experiment_id: str) -> ABExperiment:
        exp = self._require(experiment_id, ExperimentState.RUNNING)
        exp.state = ExperimentState.PAUSED
        key = (exp.tenant_id, exp.slot)
        self._active_slots.pop(key, None)
        await self._persist("update", exp)
        return exp

    async def resume(self, experiment_id: str) -> ABExperiment:
        exp = self._require(experiment_id, ExperimentState.PAUSED)
        if exp.is_expired:
            raise ExperimentFrameworkError(
                f"Experiment {experiment_id[:8]} has expired and cannot be resumed"
            )
        key = (exp.tenant_id, exp.slot)
        if key in self._active_slots:
            raise ExperimentConflictError("Another experiment started during the pause")
        exp.state = ExperimentState.RUNNING
        self._active_slots[key] = experiment_id
        await self._persist("update", exp)
        return exp

    async def complete(self, experiment_id: str) -> ABExperiment:
        exp = self._experiments.get(experiment_id)
        if exp is None:
            raise ExperimentNotFoundError(experiment_id)
        if exp.state not in (ExperimentState.RUNNING, ExperimentState.PAUSED):
            raise ExperimentFrameworkError(
                f"Cannot complete experiment in state {exp.state.value}"
            )
        exp.state = ExperimentState.COMPLETED
        key = (exp.tenant_id, exp.slot)
        self._active_slots.pop(key, None)
        await self._persist("update", exp)
        log.info("ExperimentFramework: completed experiment %s", experiment_id[:8])
        return exp

    async def cancel(self, experiment_id: str, reason: str) -> ABExperiment:
        exp = self._experiments.get(experiment_id)
        if exp is None:
            raise ExperimentNotFoundError(experiment_id)
        exp.state = ExperimentState.CANCELLED
        exp.metadata["cancellation_reason"] = reason
        key = (exp.tenant_id, exp.slot)
        self._active_slots.pop(key, None)
        await self._persist("update", exp)
        return exp

    # ── Queries ────────────────────────────────────────────────────────────────

    def get(self, experiment_id: str) -> ABExperiment | None:
        return self._experiments.get(experiment_id)

    def get_active(self, tenant_id: str, slot: str) -> ABExperiment | None:
        key = (tenant_id, slot)
        eid = self._active_slots.get(key)
        return self._experiments.get(eid) if eid else None

    def list_experiments(
        self,
        tenant_id: str,
        state:     ExperimentState | None = None,
    ) -> list[ABExperiment]:
        result = [
            e for e in self._experiments.values()
            if e.tenant_id == tenant_id
            and (state is None or e.state == state)
        ]
        return sorted(result, key=lambda e: e.created_at, reverse=True)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _require(self, experiment_id: str, expected: ExperimentState) -> ABExperiment:
        exp = self._experiments.get(experiment_id)
        if exp is None:
            raise ExperimentNotFoundError(experiment_id)
        if exp.state != expected:
            raise ExperimentFrameworkError(
                f"Experiment {experiment_id[:8]} is {exp.state.value}, "
                f"expected {expected.value}"
            )
        return exp

    async def _persist(self, op: str, exp: ABExperiment) -> None:
        if self._db_writer:
            try:
                await self._db_writer(op, exp)
            except Exception as exc:
                log.error("ExperimentFramework: persist failed: %s", exc)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ExperimentNotFoundError(Exception):
    pass

class ExperimentFrameworkError(Exception):
    pass

class ExperimentConflictError(Exception):
    pass


# ── Module-level singleton ─────────────────────────────────────────────────────

_framework: ExperimentFramework | None = None


def get_experiment_framework(db_writer: Callable | None = None) -> ExperimentFramework:
    global _framework
    if _framework is None:
        _framework = ExperimentFramework(db_writer=db_writer)
    return _framework
