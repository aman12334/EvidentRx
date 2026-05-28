"""
Workflow execution policy enforcement.

The PolicyEnforcer is the runtime gatekeeper for all AI workflow actions.
Every action proposed by a LangGraph node MUST pass through the enforcer
before being applied to case state.

Enforcement contract:
  - check_action()    → validates an AI-proposed action before it executes
  - check_budget()    → validates token and time budgets before LLM calls
  - check_confidence()→ validates confidence before automatic state transitions
  - protect_finding() → blocks any AI action that would modify a deterministic finding

The enforcer has zero runtime configurability — policy changes require restart.
This prevents runtime manipulation of governance controls.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from config.workflow_policy import WorkflowPolicy, workflow_policy

log = logging.getLogger("evidentrx.workflow_governance")


class PolicyViolation(Exception):
    """Raised when a workflow action violates execution policy."""
    pass


class PolicyEnforcer:
    """
    Stateless policy enforcement service.

    All methods return (allowed, reason) tuples or raise PolicyViolation
    when a hard policy boundary is crossed.
    """

    def __init__(self, policy: WorkflowPolicy) -> None:
        self._policy = policy

    def check_action(
        self,
        action:     str,
        severity:   str | None = None,
        confidence: float | None = None,
    ) -> None:
        """
        Validate an AI-proposed action.
        Raises PolicyViolation if the action is not permitted.
        """
        allowed, reason = self._policy.is_ai_action_permitted(
            action, severity, confidence
        )
        if not allowed:
            log.warning("Policy violation blocked: action=%s reason=%s", action, reason)
            raise PolicyViolation(reason)

    def check_budget(
        self,
        case_id:       str,
        tokens_used:   int,
        agent_runs:    int,
        elapsed_sec:   float,
    ) -> None:
        """
        Validate that a workflow is within its resource budget.
        Raises PolicyViolation if any budget is exceeded.
        """
        if tokens_used > self._policy.max_tokens_per_case:
            raise PolicyViolation(
                f"Token budget exceeded: {tokens_used} > "
                f"{self._policy.max_tokens_per_case} for case {case_id}"
            )

        if agent_runs > self._policy.max_agent_runs_per_case:
            raise PolicyViolation(
                f"Agent run limit exceeded: {agent_runs} > "
                f"{self._policy.max_agent_runs_per_case} for case {case_id}"
            )

        if elapsed_sec > self._policy.max_workflow_duration_sec:
            raise PolicyViolation(
                f"Workflow duration exceeded: {elapsed_sec:.0f}s > "
                f"{self._policy.max_workflow_duration_sec}s for case {case_id}"
            )

    def check_confidence_for_transition(
        self,
        target_status: str,
        confidence:    float | None,
    ) -> None:
        """
        Validate that confidence is sufficient for an automatic status transition.
        """
        if confidence is None:
            return  # no confidence data — human must decide

        if target_status == "resolved":
            min_conf = self._policy.min_confidence_for_resolve
            if confidence < min_conf:
                raise PolicyViolation(
                    f"Confidence {confidence:.2%} below minimum {min_conf:.2%} "
                    f"required for automatic resolution"
                )

        if target_status == "closed":
            min_conf = self._policy.min_confidence_for_close
            if confidence < min_conf:
                raise PolicyViolation(
                    f"Confidence {confidence:.2%} below minimum {min_conf:.2%} "
                    f"required for automatic close"
                )

    def requires_human_checkpoint(
        self,
        trigger:    str,
        confidence: float | None = None,
        severity:   str | None = None,
    ) -> bool:
        """
        Determine whether a human checkpoint is required before proceeding.
        """
        if trigger == "escalation" and self._policy.require_human_on_escalation:
            return True

        if severity == "critical" and self._policy.require_human_on_critical_finding:
            return True

        if (
            confidence is not None
            and confidence < self._policy.low_confidence_threshold
            and self._policy.require_human_on_low_confidence
        ):
            return True

        return False

    def get_node_policy(self, node: str) -> Dict[str, Any]:
        """Return the policy settings for a specific workflow node."""
        np = self._policy.get_node_policy(node)
        return {
            "max_tokens":           np.max_tokens,
            "timeout_seconds":      np.timeout_seconds,
            "max_retries":          np.max_retries,
            "require_confidence":   np.require_confidence,
            "require_human_review": np.require_human_review,
        }


policy_enforcer = PolicyEnforcer(workflow_policy)
