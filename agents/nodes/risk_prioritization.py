"""
risk_prioritization node — runs RiskPrioritizationAgent and persists checkpoint.
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


def risk_prioritization(state: InvestigationState, config: RunnableConfig) -> dict:
    session     = config["configurable"]["session"]
    agent       = config["configurable"]["agents"]["risk_prioritization"]
    run_id      = config["configurable"]["run_id"]
    wf_memory   = config["configurable"].get("workflow_memory") or WorkflowMemory(
        case_id=state["case_id"], run_id=run_id
    )
    case_memory = CaseMemory(case_id=state["case_id"], session=session)  # type: ignore[arg-type]

    update = agent.invoke(state, session, wf_memory, case_memory, workflow_step=3)

    merged = {**state, **update}
    _checkpoint_manager.save(
        session,
        case_id=state["case_id"],  # type: ignore[arg-type]
        run_id=run_id,
        agent_run_id=None,
        workflow_name="investigation",
        checkpoint_name="risk_prioritization",
        state=merged,
    )

    return {**update, "current_node": "risk_prioritization"}
