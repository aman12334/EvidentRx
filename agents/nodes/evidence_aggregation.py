"""
evidence_aggregation node — populates WorkflowMemory with aggregated evidence
context and runs the EvidenceAnalysisAgent.
No DB writes — reads evidence, calls agent, updates state.
"""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from agents.memory.case import CaseMemory
from agents.memory.workflow import WorkflowMemory
from agents.persistence.checkpoints import CheckpointManager
from agents.state import InvestigationState

logger = logging.getLogger(__name__)

_checkpoint_manager = CheckpointManager()


def evidence_aggregation(state: InvestigationState, config: RunnableConfig) -> dict:
    session     = config["configurable"]["session"]
    agent       = config["configurable"]["agents"]["evidence_analysis"]
    run_id      = config["configurable"]["run_id"]
    case_memory = CaseMemory(case_id=state["case_id"], session=session)  # type: ignore[arg-type]

    # Build WorkflowMemory from risk snapshot
    snap = state.get("risk_snapshot", {})
    wf_memory = WorkflowMemory(case_id=state["case_id"], run_id=run_id)
    wf_memory.findings_count = snap.get("total_findings", 0)
    wf_memory.critical_count = snap.get("by_severity", {}).get("critical", 0)
    wf_memory.high_count     = snap.get("by_severity", {}).get("high", 0)
    wf_memory.total_exposure = snap.get("total_financial_exposure")
    wf_memory.ndc_list       = snap.get("ndc_list", [])
    wf_memory.temporal_window = snap.get("temporal_window", {})

    # Store in configurable so downstream nodes can access it
    config["configurable"]["workflow_memory"] = wf_memory

    # Run EvidenceAnalysisAgent
    update = agent.invoke(
        state, session, wf_memory, case_memory, workflow_step=2
    )

    # Checkpoint after agent completes
    merged_state = {**state, **update}
    _checkpoint_manager.save(
        session,
        case_id=state["case_id"],  # type: ignore[arg-type]
        run_id=run_id,
        agent_run_id=None,
        workflow_name="investigation",
        checkpoint_name="evidence_aggregation",
        state=merged_state,
    )

    return {**update, "current_node": "evidence_aggregation"}
