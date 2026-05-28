"""
escalation_decision node — determines escalation routing and records
lifecycle transition if escalation is warranted.
No LLM call — pure orchestration logic via InvestigationOrchestratorAgent.
"""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from agents.state import InvestigationState
from investigation.services.lifecycle import InvestigationLifecycleService
from investigation.services.timeline import TimelineService

logger = logging.getLogger(__name__)

_lifecycle = InvestigationLifecycleService()
_timeline = TimelineService()


def escalation_decision(state: InvestigationState, config: RunnableConfig) -> dict:
    session      = config["configurable"]["session"]
    orchestrator = config["configurable"]["orchestrator"]

    should_escalate = orchestrator.should_escalate(state)
    risk = state.get("risk_assessment", {})

    decision = {
        "should_escalate":        should_escalate,
        "escalation_rationale":   risk.get("escalation_rationale", ""),
        "overall_risk_level":     risk.get("overall_risk_level", "unknown"),
        "remediation_urgency":    risk.get("remediation_urgency", "routine"),
        "regulatory_risk":        risk.get("regulatory_risk", {}),
    }

    # Record timeline event
    _timeline.record(
        session,
        case_id=state["case_id"],  # type: ignore[arg-type]
        event_type="STATUS_CHANGED" if should_escalate else "SNAPSHOT_TAKEN",
        event_data={
            "action":           "escalation_decision",
            "should_escalate":  should_escalate,
            "risk_level":       decision["overall_risk_level"],
            "rationale":        decision["escalation_rationale"][:500] if decision["escalation_rationale"] else "",
        },
        actor_id="risk_prioritization_agent",
        actor_type="agent",
    )

    # If escalation recommended, transition case status to ESCALATED
    if should_escalate:
        try:
            from uuid import UUID
            _lifecycle.transition(
                session,
                case_id=UUID(state["case_id"]),
                new_status="escalated",
                actor_id="investigation_workflow",
                actor_type="agent",
                notes=decision["escalation_rationale"][:500] if decision["escalation_rationale"] else None,
            )
            logger.info("Case %s escalated by workflow", state["case_id"])
        except Exception as e:
            # Non-fatal — may already be in an incompatible state
            logger.warning(
                "Could not escalate case %s: %s (status may not allow transition)",
                state["case_id"], e,
            )

    # ── Escalation gate: determine routing ────────────────────────────────────
    risk_level = decision["overall_risk_level"]
    snap = state.get("risk_snapshot", {})
    exposure = float(snap.get("total_financial_exposure") or 0)
    critical_count = snap.get("critical_findings", 0)
    deep = state.get("deep_analysis", {})
    revised = deep.get("revised_risk_assessment", "maintain")

    # HITL: critical risk OR deep analysis says escalate OR high exposure
    if (
        risk_level == "critical"
        or revised == "escalate"
        or (should_escalate and critical_count >= 10)
        or exposure >= 250_000
    ):
        escalation_route = "hitl"
    # Analyst queue: high risk or significant exposure
    elif should_escalate or risk_level == "high" or exposure >= 50_000:
        escalation_route = "analyst_queue"
    # Auto: medium/low, handled by workflow
    else:
        escalation_route = "auto"

    decision["escalation_route"] = escalation_route
    logger.info(
        "Escalation gate | case=%s | risk=%s | route=%s",
        state["case_id"], risk_level, escalation_route,
    )

    return {
        "escalation_decision": decision,
        "should_escalate":     should_escalate,
        "escalation_route":    escalation_route,
        "current_node":        "escalation_decision",
        "total_input_tokens":      0,
        "total_output_tokens":     0,
        "total_cache_read_tokens": 0,
    }


def route_after_escalation(state: InvestigationState) -> str:
    """
    Conditional edge — three routes:
      hitl         → human_review (HITL gate — flags for analyst before closing)
      analyst_queue → analyst_queue (queued for senior analyst review)
      auto          → case_summary (auto-resolved, no human needed)
    """
    route = state.get("escalation_route", "auto")
    if route == "hitl":
        return "human_review"
    if route == "analyst_queue":
        return "analyst_queue"
    return "case_summary"
