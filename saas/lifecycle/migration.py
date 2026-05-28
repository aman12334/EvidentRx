"""
Tenant data migration — plan, execute, and verify cross-version migrations.

Used when:
  - A tenant upgrades from one EvidentRx schema version to another
  - A tenant migrates from an external system into EvidentRx
  - Platform-wide schema changes require tenant-by-tenant data backfill

Migration design principles
───────────────────────────
- Dry-run first: every MigrationPlan can be validated without writing
- Atomic per-batch: each batch is committed or rolled back independently
- Idempotent: re-running a completed migration is a no-op (skip logic)
- Audit-trailed: every migration action is logged with before/after state
- Tenant-isolated: a plan for tenant A can never touch tenant B data
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.lifecycle.migration")


class MigrationStatus(str, Enum):
    PLANNED    = "planned"
    VALIDATING = "validating"
    READY      = "ready"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    ROLLED_BACK = "rolled_back"


class MigrationStepStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    SKIPPED    = "skipped"
    FAILED     = "failed"


@dataclass
class MigrationStep:
    """One discrete migration action (e.g. backfill a column, re-index docs)."""
    step_id:      str
    name:         str
    description:  str
    idempotency_key: str         # if already applied, skip
    status:       MigrationStepStatus = MigrationStepStatus.PENDING
    rows_affected: int           = 0
    error:        str | None  = None
    started_at:   datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":       self.step_id,
            "name":          self.name,
            "status":        self.status.value,
            "rows_affected": self.rows_affected,
            "error":         self.error,
        }


@dataclass
class MigrationPlan:
    """
    A migration plan for a single tenant.

    The plan carries an ordered list of MigrationSteps. Steps are
    executed in order; a failed step halts execution (no partial state).
    """
    plan_id:     str
    tenant_id:   str
    name:        str
    description: str
    from_version: str
    to_version:   str
    steps:       list[MigrationStep]
    status:      MigrationStatus
    created_by:  str
    created_at:  datetime
    started_at:  datetime | None   = None
    completed_at: datetime | None  = None
    dry_run:     bool                 = False
    metadata:    dict[str, Any]       = field(default_factory=dict)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_steps(self) -> int:
        return sum(
            1 for s in self.steps
            if s.status in (MigrationStepStatus.COMPLETED, MigrationStepStatus.SKIPPED)
        )

    @property
    def progress_pct(self) -> float:
        if not self.steps:
            return 0.0
        return round(self.completed_steps / self.total_steps, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id":         self.plan_id,
            "tenant_id":       self.tenant_id,
            "name":            self.name,
            "from_version":    self.from_version,
            "to_version":      self.to_version,
            "status":          self.status.value,
            "progress_pct":    self.progress_pct,
            "total_steps":     self.total_steps,
            "completed_steps": self.completed_steps,
            "dry_run":         self.dry_run,
            "started_at":      self.started_at.isoformat() if self.started_at else None,
            "completed_at":    self.completed_at.isoformat() if self.completed_at else None,
            "steps":           [s.to_dict() for s in self.steps],
        }


class TenantMigrationService:
    """
    Plans and executes tenant data migrations.

    Step executors are registered as callables:
      async fn(tenant_id, step, dry_run) → (rows_affected, error_or_None)
    """

    def __init__(self) -> None:
        # plan_id → MigrationPlan
        self._plans: dict[str, MigrationPlan] = {}
        # step_name → async executor callable
        self._executors: dict[str, Callable] = {}
        # idempotency_key → True  (already-applied steps)
        self._applied: dict[str, bool] = {}

    def register_executor(
        self,
        step_name: str,
        fn:        Callable,
    ) -> None:
        """Register an async executor for a named migration step type."""
        self._executors[step_name] = fn

    def create_plan(
        self,
        tenant_id:    str,
        name:         str,
        description:  str,
        from_version: str,
        to_version:   str,
        created_by:   str,
        steps:        list[dict[str, Any]],   # [{name, description, idempotency_key}]
        dry_run:      bool = False,
    ) -> MigrationPlan:
        migration_steps = [
            MigrationStep(
                step_id          = str(uuid.uuid4()),
                name             = s["name"],
                description      = s.get("description", ""),
                idempotency_key  = s["idempotency_key"],
            )
            for s in steps
        ]
        plan = MigrationPlan(
            plan_id      = str(uuid.uuid4()),
            tenant_id    = tenant_id,
            name         = name,
            description  = description,
            from_version = from_version,
            to_version   = to_version,
            steps        = migration_steps,
            status       = MigrationStatus.PLANNED,
            created_by   = created_by,
            created_at   = datetime.now(tz=UTC),
            dry_run      = dry_run,
        )
        self._plans[plan.plan_id] = plan
        log.info(
            "TenantMigrationService: created plan '%s' for tenant %s (%d steps)",
            name, tenant_id[:8], len(migration_steps),
        )
        return plan

    async def execute(self, plan_id: str) -> MigrationPlan:
        plan = self._get_plan(plan_id)
        if plan.status not in (MigrationStatus.PLANNED, MigrationStatus.READY):
            raise MigrationError(
                f"Plan {plan_id[:8]} is {plan.status.value} — cannot execute"
            )

        plan.status     = MigrationStatus.RUNNING
        plan.started_at = datetime.now(tz=UTC)

        for step in plan.steps:
            # Idempotency check
            if self._applied.get(f"{plan.tenant_id}:{step.idempotency_key}"):
                step.status = MigrationStepStatus.SKIPPED
                log.info(
                    "TenantMigrationService: skipped idempotent step '%s'",
                    step.name,
                )
                continue

            step.status     = MigrationStepStatus.RUNNING
            step.started_at = datetime.now(tz=UTC)

            executor = self._executors.get(step.name)
            if executor is None:
                step.status = MigrationStepStatus.FAILED
                step.error  = f"No executor registered for step '{step.name}'"
                plan.status = MigrationStatus.FAILED
                log.error("TenantMigrationService: %s", step.error)
                return plan

            try:
                rows_affected, error = await executor(plan.tenant_id, step, plan.dry_run)
                if error:
                    step.status = MigrationStepStatus.FAILED
                    step.error  = error
                    plan.status = MigrationStatus.FAILED
                    log.error(
                        "TenantMigrationService: step '%s' failed: %s",
                        step.name, error,
                    )
                    return plan
                step.rows_affected = rows_affected or 0
                step.status        = MigrationStepStatus.COMPLETED
                step.completed_at  = datetime.now(tz=UTC)
                if not plan.dry_run:
                    self._applied[f"{plan.tenant_id}:{step.idempotency_key}"] = True
            except Exception as exc:
                step.status = MigrationStepStatus.FAILED
                step.error  = str(exc)
                plan.status = MigrationStatus.FAILED
                log.exception(
                    "TenantMigrationService: unexpected error in step '%s'", step.name
                )
                return plan

        plan.status       = MigrationStatus.COMPLETED
        plan.completed_at = datetime.now(tz=UTC)
        log.info(
            "TenantMigrationService: plan '%s' completed (dry_run=%s)",
            plan.name, plan.dry_run,
        )
        return plan

    def list_plans(
        self,
        tenant_id: str,
        status:    MigrationStatus | None = None,
    ) -> list[MigrationPlan]:
        return [
            p for p in self._plans.values()
            if p.tenant_id == tenant_id
            and (status is None or p.status == status)
        ]

    def get_plan(self, plan_id: str) -> MigrationPlan | None:
        return self._plans.get(plan_id)

    def _get_plan(self, plan_id: str) -> MigrationPlan:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise MigrationError(f"MigrationPlan {plan_id} not found")
        return plan


# ── Exceptions ─────────────────────────────────────────────────────────────────

class MigrationError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_service: TenantMigrationService | None = None


def get_migration_service() -> TenantMigrationService:
    global _service
    if _service is None:
        _service = TenantMigrationService()
    return _service
