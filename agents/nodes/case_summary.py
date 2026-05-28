"""
case_summary node — compiles the final investigation summary, persists it
to the investigation_case record, and marks the workflow complete.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig
from sqlalchemy import text

from agents.persistence.checkpoints import CheckpointManager
from agents.state import InvestigationState
from investigation.services.timeline import TimelineService

logger = logging.getLogger(__name__)

_checkpoint_manager = CheckpointManager()
_timeline = TimelineService()


def case_summary(state: InvestigationState, config: RunnableConfig) -> dict:
    session     = config["configurable"]["session"]
    orchestrator = config["configurable"]["orchestrator"]
    run_id      = config["configurable"]["run_id"]

    now = datetime.now(UTC)
    workflow_summary = orchestrator.get_workflow_summary(state)

    # Compile final case summary
    narrative = state.get("narrative", {})
    risk      = state.get("risk_assessment", {})
    evidence  = state.get("evidence_analysis", {})

    summary = {
        "workflow":             workflow_summary,
        "executive_summary":    narrative.get("executive_summary", ""),
        "technical_findings":   narrative.get("technical_findings", ""),
        "regulatory_context":   narrative.get("regulatory_context", ""),
        "remediation":          narrative.get("remediation_recommendations", []),
        "audit_preparation":    narrative.get("audit_preparation_notes", ""),
        "risk_level":           risk.get("overall_risk_level"),
        "escalated":            state.get("should_escalate", False),
        "patterns_identified":  len(state.get("patterns", [])),
        "audit_defensibility":  evidence.get("audit_defensibility_score"),
        "generated_at":         now.isoformat(),
    }

    # Persist narrative to investigation_case (financial_exposure_estimate from agent)
    exposure_assessment = risk.get("financial_exposure_assessment", {})
    exposure_max = exposure_assessment.get("maximum_estimate_usd")

    if exposure_max:
        session.execute(text("""
            UPDATE audit.investigation_cases
            SET financial_exposure_estimate = :exposure,
                last_agent_activity_at = :now
            WHERE case_id = :case_id
        """), {
            "exposure": float(exposure_max),
            "now":      now,
            "case_id":  state["case_id"],
        })

    # Record AGENT_COMPLETED timeline event
    _timeline.record(
        session,
        case_id=state["case_id"],   # type: ignore[arg-type]
        event_type="AGENT_COMPLETED",
        event_data={
            "workflow":               "investigation",
            "run_id":                 run_id,
            "total_input_tokens":     state.get("total_input_tokens", 0),
            "total_output_tokens":    state.get("total_output_tokens", 0),
            "errors_count":           len(state.get("errors", [])),
            "escalated":              state.get("should_escalate", False),
        },
        actor_id="investigation_workflow",
        actor_type="agent",
    )

    # Final checkpoint — not resumable (workflow complete)
    merged = {**state, "case_summary": summary, "is_complete": True}
    _checkpoint_manager.save(
        session,
        case_id=state["case_id"],   # type: ignore[arg-type]
        run_id=run_id,
        agent_run_id=None,
        workflow_name="investigation",
        checkpoint_name="case_summary",
        state=merged,
        is_resumable=False,         # terminal checkpoint
    )

    session.flush()

    logger.info(
        "case_summary: workflow complete for case %s — "
        "escalated=%s tokens=%d/%d",
        state["case_id"],
        state.get("should_escalate"),
        state.get("total_input_tokens", 0),
        state.get("total_output_tokens", 0),
    )

    return {
        "case_summary":  summary,
        "is_complete":   True,
        "completed_at":  now.isoformat(),
        "current_node":  "case_summary",
        "total_input_tokens":      0,
        "total_output_tokens":     0,
        "total_cache_read_tokens": 0,
    }
