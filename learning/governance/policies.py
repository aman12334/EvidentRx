"""
Learning governance policies.

Defines the policy layer that gates what the learning system is permitted
to do. Policies are evaluated before any calibration change, prompt
promotion, or experiment is allowed to affect production behaviour.

Policy design
─────────────
  - Fail-closed: if any policy fails, the action is blocked
  - Composable: multiple policies evaluated in sequence
  - Auditable: every evaluation produces a structured PolicyResult
  - Configurable: min thresholds and requirements injected at construction

Built-in policies
─────────────────
  MinSampleSizePolicy        — requires sufficient feedback before calibration
  CalibrationDriftPolicy     — blocks activation if ECE drift exceeds threshold
  ApprovalRequiredPolicy     — blocks actions without an approved ApprovalRequest
  ExperimentGuardrailPolicy  — prevents experiments from exceeding safety limits
  PromptCoveragePolicy       — blocks promotion if test coverage is too low
  SelfApprovalPolicy         — blocks self-approved changes
  RollbackRateLimitPolicy    — limits rollback frequency to prevent thrashing
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("evidentrx.learning.governance.policies")


@dataclass
class PolicyContext:
    """Context passed to every policy for evaluation."""
    tenant_id:     str
    actor:         str
    action:        str           # e.g. "calibration_activate", "prompt_promote"
    artifact_id:   str
    artifact_type: str
    payload:       dict[str, Any] = field(default_factory=dict)
    metadata:      dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyResult:
    """Result of evaluating one policy."""
    policy_name: str
    passed:      bool
    reason:      str
    metadata:    dict[str, Any] = field(default_factory=dict)


class LearningPolicy(ABC):
    """Abstract base class for all learning governance policies."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        ...


# ── Built-in policies ──────────────────────────────────────────────────────────

class MinSampleSizePolicy(LearningPolicy):
    """
    Blocks calibration activation if the feedback sample count is below
    the configured minimum.
    """

    def __init__(self, min_samples: int = 10) -> None:
        self._min = min_samples

    @property
    def name(self) -> str:
        return "min_sample_size"

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if context.action not in ("calibration_activate", "calibration_approve"):
            return PolicyResult(self.name, True, "Action not subject to sample check")
        sample_count = context.payload.get("sample_count", 0)
        if sample_count >= self._min:
            return PolicyResult(
                self.name, True,
                f"Sample count {sample_count} meets minimum {self._min}",
            )
        return PolicyResult(
            self.name, False,
            f"Sample count {sample_count} is below minimum {self._min}; "
            "more feedback required before calibration can be activated",
        )


class CalibrationDriftPolicy(LearningPolicy):
    """
    Blocks calibration activation if the Expected Calibration Error (ECE)
    is too high, indicating the calibration is unreliable.
    """

    def __init__(self, max_ece: float = 0.15) -> None:
        self._max_ece = max_ece

    @property
    def name(self) -> str:
        return "calibration_drift"

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if context.action not in ("calibration_activate",):
            return PolicyResult(self.name, True, "Action not subject to drift check")
        ece = context.payload.get("ece")
        if ece is None:
            return PolicyResult(self.name, True, "ECE not provided; check skipped")
        if ece <= self._max_ece:
            return PolicyResult(
                self.name, True,
                f"ECE {ece:.4f} is within acceptable limit {self._max_ece}",
            )
        return PolicyResult(
            self.name, False,
            f"ECE {ece:.4f} exceeds maximum {self._max_ece}; "
            "calibration snapshot may not be reliable",
        )


class ApprovalRequiredPolicy(LearningPolicy):
    """
    Blocks production changes that have not received explicit approval.
    """

    APPROVAL_REQUIRED_ACTIONS = frozenset({
        "calibration_activate",
        "prompt_promote",
        "workflow_promote",
        "experiment_start",
        "template_promote",
        "threshold_adjust",
        "memory_purge",
    })

    @property
    def name(self) -> str:
        return "approval_required"

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if context.action not in self.APPROVAL_REQUIRED_ACTIONS:
            return PolicyResult(self.name, True, "Action does not require approval")
        approval_status = context.payload.get("approval_status")
        if approval_status == "approved":
            return PolicyResult(self.name, True, "Approval confirmed")
        return PolicyResult(
            self.name, False,
            f"Action '{context.action}' requires an approved ApprovalRequest; "
            f"current status: {approval_status or 'not provided'}",
        )


class PromptCoveragePolicy(LearningPolicy):
    """
    Blocks prompt promotion if test coverage is below the minimum threshold.
    """

    def __init__(self, min_coverage: float = 0.70) -> None:
        self._min = min_coverage

    @property
    def name(self) -> str:
        return "prompt_coverage"

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if context.action not in ("prompt_promote", "prompt_review"):
            return PolicyResult(self.name, True, "Action not subject to coverage check")
        coverage = context.payload.get("test_coverage", 0.0)
        if coverage >= self._min:
            return PolicyResult(
                self.name, True,
                f"Test coverage {coverage:.2f} meets minimum {self._min:.2f}",
            )
        return PolicyResult(
            self.name, False,
            f"Prompt test coverage {coverage:.2f} is below minimum {self._min:.2f}; "
            "run benchmark evaluation before promotion",
        )


class SelfApprovalPolicy(LearningPolicy):
    """
    Prevents an actor from approving their own change requests.
    """

    @property
    def name(self) -> str:
        return "self_approval"

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if "approve" not in context.action:
            return PolicyResult(self.name, True, "Not an approval action")
        requester = context.payload.get("requested_by")
        if requester and requester == context.actor:
            return PolicyResult(
                self.name, False,
                f"Actor '{context.actor}' cannot approve their own request",
            )
        return PolicyResult(self.name, True, "Self-approval check passed")


class ExperimentGuardrailPolicy(LearningPolicy):
    """
    Prevents experiments from running with excessive traffic fractions
    or without a defined stop_at.
    """

    def __init__(self, max_traffic_fraction: float = 0.50) -> None:
        self._max_traffic = max_traffic_fraction

    @property
    def name(self) -> str:
        return "experiment_guardrail"

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if context.action not in ("experiment_start", "experiment_create"):
            return PolicyResult(self.name, True, "Not an experiment action")

        fraction = context.payload.get("traffic_fraction", 0.0)
        if fraction > self._max_traffic:
            return PolicyResult(
                self.name, False,
                f"Traffic fraction {fraction:.2f} exceeds maximum {self._max_traffic:.2f}; "
                "reduce traffic fraction before starting experiment",
            )

        stop_at = context.payload.get("stop_at")
        if stop_at is None:
            return PolicyResult(
                self.name, False,
                "Experiment must have a defined stop_at timestamp",
            )

        return PolicyResult(self.name, True, "Experiment guardrails satisfied")


class RollbackRateLimitPolicy(LearningPolicy):
    """
    Limits how frequently rollbacks can occur for a given tenant + slot
    within a rolling time window (prevents thrashing).
    """

    def __init__(
        self,
        max_rollbacks:  int = 3,
        window_hours:   int = 24,
    ) -> None:
        self._max        = max_rollbacks
        self._window     = timedelta(hours=window_hours)
        # (tenant_id, slot) → list of rollback timestamps
        self._history:   dict[tuple[str, str], list[datetime]] = {}

    @property
    def name(self) -> str:
        return "rollback_rate_limit"

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if "rollback" not in context.action:
            return PolicyResult(self.name, True, "Not a rollback action")

        slot      = context.payload.get("slot", context.artifact_id)
        key       = (context.tenant_id, slot)
        now       = datetime.now(tz=UTC)
        cutoff    = now - self._window
        recent    = [t for t in self._history.get(key, []) if t >= cutoff]

        if len(recent) >= self._max:
            return PolicyResult(
                self.name, False,
                f"Rollback rate limit: {len(recent)} rollbacks in the last "
                f"{self._window.total_seconds()/3600:.0f}h "
                f"(max={self._max}). Wait before rolling back again.",
            )

        # Record this rollback
        self._history.setdefault(key, []).append(now)
        # Prune old entries
        self._history[key] = [t for t in self._history[key] if t >= cutoff]

        return PolicyResult(
            self.name, True,
            f"Rollback rate check passed ({len(recent) + 1}/{self._max})",
        )


# ── Policy engine ──────────────────────────────────────────────────────────────

class LearningPolicyEngine:
    """
    Evaluates a list of policies against a PolicyContext.

    Fail-closed: any failing policy blocks the action.
    """

    def __init__(self, policies: list[LearningPolicy] | None = None) -> None:
        self._policies: list[LearningPolicy] = policies or _default_policies()

    def evaluate(
        self,
        context: PolicyContext,
    ) -> tuple[bool, list[PolicyResult]]:
        """
        Evaluate all policies.

        Returns (all_passed, results_list). If all_passed is False the
        action must be blocked.
        """
        results: list[PolicyResult] = []
        all_passed = True
        for policy in self._policies:
            result = policy.evaluate(context)
            results.append(result)
            if not result.passed:
                all_passed = False
                log.warning(
                    "LearningPolicy '%s' FAILED for actor=%s action=%s: %s",
                    policy.name, context.actor, context.action, result.reason,
                )
        return all_passed, results

    def add_policy(self, policy: LearningPolicy) -> None:
        self._policies.append(policy)


def _default_policies() -> list[LearningPolicy]:
    return [
        MinSampleSizePolicy(),
        CalibrationDriftPolicy(),
        ApprovalRequiredPolicy(),
        PromptCoveragePolicy(),
        SelfApprovalPolicy(),
        ExperimentGuardrailPolicy(),
        RollbackRateLimitPolicy(),
    ]


# ── Module-level singleton ─────────────────────────────────────────────────────

_engine: LearningPolicyEngine | None = None


def get_policy_engine(
    extra_policies: list[LearningPolicy] | None = None,
) -> LearningPolicyEngine:
    global _engine
    if _engine is None:
        policies = _default_policies()
        if extra_policies:
            policies.extend(extra_policies)
        _engine = LearningPolicyEngine(policies=policies)
    return _engine
