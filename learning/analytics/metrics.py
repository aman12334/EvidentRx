"""
Prometheus metrics for the learning layer.

Exposes operational telemetry from the continuous learning system to the
observability stack. Metrics are registered once at startup and updated
by each subsystem component.

Metric categories
─────────────────
  Feedback    — submission rates, duplicate rate, lineage chain depth
  Calibration — snapshot activations, ECE, drift alerts
  Evaluation  — run counts, accuracy, hallucination rate, latency
  Versioning  — prompt/workflow promotion counts, rollback counts
  Governance  — approval request counts, policy violations, audit events
  Experiments — active experiment count, arm assignment counts
"""

from __future__ import annotations

import logging

log = logging.getLogger("evidentrx.learning.analytics.metrics")

# Lazily imported prometheus_client objects
_metrics: dict = {}
_registered: bool = False


def register_metrics() -> None:
    """
    Register all Prometheus metrics for the learning layer.

    Idempotent — safe to call multiple times. Silently skips registration
    if prometheus_client is not installed.
    """
    global _registered, _metrics
    if _registered:
        return
    try:
        from prometheus_client import Counter, Gauge, Histogram

        _metrics = {
            # ── Feedback ─────────────────────────────────────────────────────
            "feedback_submitted_total": Counter(
                "evidentrx_learning_feedback_submitted_total",
                "Total feedback records submitted",
                ["tenant_id", "feedback_type"],
            ),
            "feedback_duplicates_total": Counter(
                "evidentrx_learning_feedback_duplicates_total",
                "Duplicate feedback records rejected",
                ["tenant_id"],
            ),
            "feedback_validation_errors_total": Counter(
                "evidentrx_learning_feedback_validation_errors_total",
                "Feedback records rejected due to validation errors",
                ["tenant_id"],
            ),
            "feedback_lineage_chain_depth": Gauge(
                "evidentrx_learning_feedback_lineage_chain_depth",
                "Current depth of the feedback lineage chain per tenant",
                ["tenant_id"],
            ),

            # ── Calibration ───────────────────────────────────────────────────
            "calibration_activations_total": Counter(
                "evidentrx_learning_calibration_activations_total",
                "Number of calibration snapshots activated",
                ["tenant_id"],
            ),
            "calibration_rollbacks_total": Counter(
                "evidentrx_learning_calibration_rollbacks_total",
                "Number of calibration rollbacks performed",
                ["tenant_id"],
            ),
            "calibration_ece": Gauge(
                "evidentrx_learning_calibration_ece",
                "Current Expected Calibration Error (ECE) per tenant",
                ["tenant_id"],
            ),
            "calibration_drift_alerts_total": Counter(
                "evidentrx_learning_calibration_drift_alerts_total",
                "Number of calibration drift alerts fired",
                ["tenant_id", "severity"],
            ),

            # ── Evaluation ────────────────────────────────────────────────────
            "evaluation_runs_total": Counter(
                "evidentrx_learning_evaluation_runs_total",
                "Total evaluation harness runs",
                ["tenant_id", "evaluation_type", "status"],
            ),
            "evaluation_outcome_accuracy": Gauge(
                "evidentrx_learning_evaluation_outcome_accuracy",
                "Latest evaluation run outcome accuracy per tenant",
                ["tenant_id"],
            ),
            "evaluation_hallucination_rate": Gauge(
                "evidentrx_learning_evaluation_hallucination_rate",
                "Latest evaluation run hallucination rate per tenant",
                ["tenant_id"],
            ),
            "evaluation_latency_seconds": Histogram(
                "evidentrx_learning_evaluation_latency_seconds",
                "Evaluation run total duration in seconds",
                ["tenant_id"],
                buckets=[10, 30, 60, 120, 300, 600, 1800],
            ),

            # ── Versioning ────────────────────────────────────────────────────
            "prompt_promotions_total": Counter(
                "evidentrx_learning_prompt_promotions_total",
                "Number of prompt versions promoted to ACTIVE",
                ["tenant_id", "prompt_name"],
            ),
            "prompt_rollbacks_total": Counter(
                "evidentrx_learning_prompt_rollbacks_total",
                "Number of prompt version rollbacks",
                ["tenant_id", "prompt_name"],
            ),
            "workflow_promotions_total": Counter(
                "evidentrx_learning_workflow_promotions_total",
                "Number of workflow versions promoted to ACTIVE",
                ["tenant_id", "workflow_name"],
            ),

            # ── Governance ────────────────────────────────────────────────────
            "approval_requests_total": Counter(
                "evidentrx_learning_approval_requests_total",
                "Total approval requests created",
                ["tenant_id", "change_type"],
            ),
            "approval_decisions_total": Counter(
                "evidentrx_learning_approval_decisions_total",
                "Total approval decisions (approved/rejected)",
                ["tenant_id", "decision"],
            ),
            "policy_violations_total": Counter(
                "evidentrx_learning_policy_violations_total",
                "Total learning governance policy violations",
                ["tenant_id", "policy_name"],
            ),
            "audit_events_total": Counter(
                "evidentrx_learning_audit_events_total",
                "Total learning governance audit events logged",
                ["tenant_id", "event_type"],
            ),
            "pending_approvals": Gauge(
                "evidentrx_learning_pending_approvals",
                "Number of pending approval requests per tenant",
                ["tenant_id"],
            ),

            # ── Experiments ───────────────────────────────────────────────────
            "active_experiments": Gauge(
                "evidentrx_learning_active_experiments",
                "Number of currently running experiments per tenant",
                ["tenant_id"],
            ),
            "experiment_arm_assignments_total": Counter(
                "evidentrx_learning_experiment_arm_assignments_total",
                "Total entity-to-arm assignments",
                ["tenant_id", "arm"],
            ),

            # ── Recommendation ────────────────────────────────────────────────
            "recommendation_follow_rate": Gauge(
                "evidentrx_learning_recommendation_follow_rate",
                "Latest recommendation follow rate per tenant",
                ["tenant_id"],
            ),
            "recommendation_effectiveness_rate": Gauge(
                "evidentrx_learning_recommendation_effectiveness_rate",
                "Latest recommendation effectiveness rate per tenant",
                ["tenant_id"],
            ),

            # ── Memory ────────────────────────────────────────────────────────
            "memory_entries_total": Gauge(
                "evidentrx_learning_memory_entries_total",
                "Total active memory entries per tenant",
                ["tenant_id", "memory_type"],
            ),
        }

        _registered = True
        log.info("LearningMetrics: Prometheus metrics registered (%d metrics)", len(_metrics))

    except ImportError:
        log.warning("LearningMetrics: prometheus_client not installed; metrics disabled")
        _registered = True   # prevent repeated attempts


# ── Metric update helpers ──────────────────────────────────────────────────────

def inc_feedback_submitted(tenant_id: str, feedback_type: str) -> None:
    m = _metrics.get("feedback_submitted_total")
    if m:
        m.labels(tenant_id=tenant_id, feedback_type=feedback_type).inc()


def inc_feedback_duplicate(tenant_id: str) -> None:
    m = _metrics.get("feedback_duplicates_total")
    if m:
        m.labels(tenant_id=tenant_id).inc()


def inc_feedback_validation_error(tenant_id: str) -> None:
    m = _metrics.get("feedback_validation_errors_total")
    if m:
        m.labels(tenant_id=tenant_id).inc()


def set_feedback_chain_depth(tenant_id: str, depth: int) -> None:
    m = _metrics.get("feedback_lineage_chain_depth")
    if m:
        m.labels(tenant_id=tenant_id).set(depth)


def inc_calibration_activation(tenant_id: str) -> None:
    m = _metrics.get("calibration_activations_total")
    if m:
        m.labels(tenant_id=tenant_id).inc()


def inc_calibration_rollback(tenant_id: str) -> None:
    m = _metrics.get("calibration_rollbacks_total")
    if m:
        m.labels(tenant_id=tenant_id).inc()


def set_calibration_ece(tenant_id: str, ece: float) -> None:
    m = _metrics.get("calibration_ece")
    if m:
        m.labels(tenant_id=tenant_id).set(ece)


def inc_calibration_drift_alert(tenant_id: str, severity: str) -> None:
    m = _metrics.get("calibration_drift_alerts_total")
    if m:
        m.labels(tenant_id=tenant_id, severity=severity).inc()


def inc_evaluation_run(tenant_id: str, evaluation_type: str, status: str) -> None:
    m = _metrics.get("evaluation_runs_total")
    if m:
        m.labels(tenant_id=tenant_id, evaluation_type=evaluation_type, status=status).inc()


def set_evaluation_accuracy(tenant_id: str, accuracy: float) -> None:
    m = _metrics.get("evaluation_outcome_accuracy")
    if m:
        m.labels(tenant_id=tenant_id).set(accuracy)


def set_evaluation_hallucination_rate(tenant_id: str, rate: float) -> None:
    m = _metrics.get("evaluation_hallucination_rate")
    if m:
        m.labels(tenant_id=tenant_id).set(rate)


def observe_evaluation_latency(tenant_id: str, seconds: float) -> None:
    m = _metrics.get("evaluation_latency_seconds")
    if m:
        m.labels(tenant_id=tenant_id).observe(seconds)


def inc_prompt_promotion(tenant_id: str, prompt_name: str) -> None:
    m = _metrics.get("prompt_promotions_total")
    if m:
        m.labels(tenant_id=tenant_id, prompt_name=prompt_name).inc()


def inc_prompt_rollback(tenant_id: str, prompt_name: str) -> None:
    m = _metrics.get("prompt_rollbacks_total")
    if m:
        m.labels(tenant_id=tenant_id, prompt_name=prompt_name).inc()


def inc_workflow_promotion(tenant_id: str, workflow_name: str) -> None:
    m = _metrics.get("workflow_promotions_total")
    if m:
        m.labels(tenant_id=tenant_id, workflow_name=workflow_name).inc()


def inc_approval_request(tenant_id: str, change_type: str) -> None:
    m = _metrics.get("approval_requests_total")
    if m:
        m.labels(tenant_id=tenant_id, change_type=change_type).inc()


def inc_approval_decision(tenant_id: str, decision: str) -> None:
    m = _metrics.get("approval_decisions_total")
    if m:
        m.labels(tenant_id=tenant_id, decision=decision).inc()


def inc_policy_violation(tenant_id: str, policy_name: str) -> None:
    m = _metrics.get("policy_violations_total")
    if m:
        m.labels(tenant_id=tenant_id, policy_name=policy_name).inc()


def inc_audit_event(tenant_id: str, event_type: str) -> None:
    m = _metrics.get("audit_events_total")
    if m:
        m.labels(tenant_id=tenant_id, event_type=event_type).inc()


def set_pending_approvals(tenant_id: str, count: int) -> None:
    m = _metrics.get("pending_approvals")
    if m:
        m.labels(tenant_id=tenant_id).set(count)


def set_active_experiments(tenant_id: str, count: int) -> None:
    m = _metrics.get("active_experiments")
    if m:
        m.labels(tenant_id=tenant_id).set(count)


def inc_experiment_assignment(tenant_id: str, arm: str) -> None:
    m = _metrics.get("experiment_arm_assignments_total")
    if m:
        m.labels(tenant_id=tenant_id, arm=arm).inc()


def set_recommendation_follow_rate(tenant_id: str, rate: float) -> None:
    m = _metrics.get("recommendation_follow_rate")
    if m:
        m.labels(tenant_id=tenant_id).set(rate)


def set_recommendation_effectiveness_rate(tenant_id: str, rate: float) -> None:
    m = _metrics.get("recommendation_effectiveness_rate")
    if m:
        m.labels(tenant_id=tenant_id).set(rate)


def set_memory_entries(tenant_id: str, memory_type: str, count: int) -> None:
    m = _metrics.get("memory_entries_total")
    if m:
        m.labels(tenant_id=tenant_id, memory_type=memory_type).set(count)
