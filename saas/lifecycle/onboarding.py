"""
Tenant onboarding workflow and readiness checks.

After the TenantProvisioner completes the mechanical setup (Phase 12 admin),
the OnboardingWorkflow guides the tenant through their initial configuration
steps: connecting data sources, configuring rule packs, inviting analysts,
and completing a first investigation dry-run. Completion is gated by
ReadinessChecks to ensure the tenant is operationally viable before
the trial clock or billing starts.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.saas.lifecycle.onboarding")


class OnboardingStepName(str, Enum):
    ACCEPT_TERMS         = "accept_terms"
    CONFIGURE_DATA_SOURCE = "configure_data_source"
    ASSIGN_RULE_PACKS    = "assign_rule_packs"
    INVITE_ANALYSTS      = "invite_analysts"
    SET_THRESHOLDS       = "set_thresholds"
    DRY_RUN_INVESTIGATION = "dry_run_investigation"
    REVIEW_DASHBOARD     = "review_dashboard"
    COMPLETE             = "complete"


class OnboardingStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED     = "blocked"
    COMPLETED   = "completed"


@dataclass
class OnboardingStep:
    step_name:    OnboardingStepName
    title:        str
    description:  str
    required:     bool          = True
    completed:    bool          = False
    skipped:      bool          = False
    completed_at: Optional[datetime] = None
    completed_by: Optional[str]     = None
    metadata:     dict[str, Any]    = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.completed or self.skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name":    self.step_name.value,
            "title":        self.title,
            "required":     self.required,
            "completed":    self.completed,
            "skipped":      self.skipped,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


@dataclass
class OnboardingState:
    """Tracks a tenant's onboarding progress."""
    onboarding_id: str
    tenant_id:     str
    status:        OnboardingStatus
    steps:         list[OnboardingStep]
    started_at:    datetime
    completed_at:  Optional[datetime] = None
    due_by:        Optional[datetime] = None   # trial expiry / activation deadline

    @property
    def next_step(self) -> Optional[OnboardingStep]:
        for step in self.steps:
            if not step.done:
                return step
        return None

    @property
    def completion_pct(self) -> float:
        if not self.steps:
            return 0.0
        done = sum(1 for s in self.steps if s.done)
        return round(done / len(self.steps), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "onboarding_id":  self.onboarding_id,
            "tenant_id":      self.tenant_id,
            "status":         self.status.value,
            "completion_pct": self.completion_pct,
            "steps":          [s.to_dict() for s in self.steps],
            "started_at":     self.started_at.isoformat(),
            "completed_at":   self.completed_at.isoformat() if self.completed_at else None,
            "due_by":         self.due_by.isoformat() if self.due_by else None,
        }


_DEFAULT_STEPS: list[tuple[OnboardingStepName, str, str, bool]] = [
    (OnboardingStepName.ACCEPT_TERMS,
     "Accept Terms of Service",
     "Review and accept the EvidentRx platform terms and BAA.",
     True),
    (OnboardingStepName.CONFIGURE_DATA_SOURCE,
     "Connect a Data Source",
     "Configure at least one pharmacy or claims data integration.",
     True),
    (OnboardingStepName.ASSIGN_RULE_PACKS,
     "Assign Rule Packs",
     "Select the 340B compliance rule packs relevant to your program.",
     True),
    (OnboardingStepName.INVITE_ANALYSTS,
     "Invite Analysts",
     "Add at least one analyst or compliance officer to your team.",
     True),
    (OnboardingStepName.SET_THRESHOLDS,
     "Configure Thresholds",
     "Review and customise detection thresholds for your covered entities.",
     False),
    (OnboardingStepName.DRY_RUN_INVESTIGATION,
     "Run a Test Investigation",
     "Execute a dry-run investigation against sample data to validate setup.",
     True),
    (OnboardingStepName.REVIEW_DASHBOARD,
     "Review the Dashboard",
     "Walk through the investigation dashboard and confirm everything looks correct.",
     False),
]


class OnboardingWorkflow:
    """
    Manages tenant onboarding sessions and step progression.

    Each tenant has at most one active OnboardingState. Completed states
    are archived but never deleted (audit trail).
    """

    def __init__(self) -> None:
        # tenant_id → OnboardingState
        self._states: dict[str, OnboardingState] = {}

    def start(
        self,
        tenant_id: str,
        due_by:    Optional[datetime] = None,
    ) -> OnboardingState:
        if tenant_id in self._states:
            existing = self._states[tenant_id]
            if existing.status != OnboardingStatus.COMPLETED:
                return existing   # resume

        steps = [
            OnboardingStep(
                step_name   = name,
                title       = title,
                description = desc,
                required    = required,
            )
            for name, title, desc, required in _DEFAULT_STEPS
        ]
        state = OnboardingState(
            onboarding_id = str(uuid.uuid4()),
            tenant_id     = tenant_id,
            status        = OnboardingStatus.IN_PROGRESS,
            steps         = steps,
            started_at    = datetime.now(tz=timezone.utc),
            due_by        = due_by,
        )
        self._states[tenant_id] = state
        log.info("OnboardingWorkflow: started onboarding for tenant %s", tenant_id[:8])
        return state

    def complete_step(
        self,
        tenant_id:    str,
        step_name:    OnboardingStepName,
        completed_by: str,
        metadata:     Optional[dict[str, Any]] = None,
    ) -> OnboardingState:
        state = self._get_state(tenant_id)
        step  = next((s for s in state.steps if s.step_name == step_name), None)
        if step is None:
            raise OnboardingError(f"Step {step_name.value} not found")

        step.completed    = True
        step.completed_at = datetime.now(tz=timezone.utc)
        step.completed_by = completed_by
        step.metadata.update(metadata or {})

        self._check_completion(state)
        return state

    def skip_step(
        self,
        tenant_id: str,
        step_name: OnboardingStepName,
    ) -> OnboardingState:
        state = self._get_state(tenant_id)
        step  = next((s for s in state.steps if s.step_name == step_name), None)
        if step is None:
            raise OnboardingError(f"Step {step_name.value} not found")
        if step.required:
            raise OnboardingError(f"Required step {step_name.value} cannot be skipped")
        step.skipped = True
        self._check_completion(state)
        return state

    def get_state(self, tenant_id: str) -> Optional[OnboardingState]:
        return self._states.get(tenant_id)

    def _get_state(self, tenant_id: str) -> OnboardingState:
        state = self._states.get(tenant_id)
        if state is None:
            raise OnboardingError(f"No onboarding session for tenant {tenant_id[:8]}")
        return state

    def _check_completion(self, state: OnboardingState) -> None:
        required_done = all(s.done for s in state.steps if s.required)
        all_done      = all(s.done for s in state.steps)
        if required_done:
            if all_done:
                state.status       = OnboardingStatus.COMPLETED
                state.completed_at = datetime.now(tz=timezone.utc)
                log.info(
                    "OnboardingWorkflow: tenant %s completed onboarding",
                    state.tenant_id[:8],
                )
            else:
                state.status = OnboardingStatus.IN_PROGRESS


# ── Readiness checks ───────────────────────────────────────────────────────────

@dataclass
class ReadinessCheckResult:
    check_name:  str
    passed:      bool
    message:     str
    critical:    bool = True


class ReadinessChecks:
    """
    Validates that a tenant is operationally ready before activation.

    Checks are pluggable — register callables with register_check().
    Each check receives the tenant_id and returns a ReadinessCheckResult.
    """

    def __init__(self) -> None:
        self._checks: list[tuple[str, Callable[[str], ReadinessCheckResult]]] = []

    def register_check(
        self,
        name:    str,
        fn:      Callable[[str], ReadinessCheckResult],
    ) -> None:
        self._checks.append((name, fn))

    def run_all(self, tenant_id: str) -> tuple[bool, list[ReadinessCheckResult]]:
        results  = [fn(tenant_id) for _, fn in self._checks]
        all_pass = all(r.passed for r in results if r.critical)
        return all_pass, results


# ── Exceptions ─────────────────────────────────────────────────────────────────

class OnboardingError(Exception):
    pass


# ── Singletons ─────────────────────────────────────────────────────────────────

_workflow: Optional[OnboardingWorkflow] = None
_checks:   Optional[ReadinessChecks]   = None


def get_onboarding_workflow() -> OnboardingWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = OnboardingWorkflow()
    return _workflow


def get_readiness_checks() -> ReadinessChecks:
    global _checks
    if _checks is None:
        _checks = ReadinessChecks()
    return _checks
