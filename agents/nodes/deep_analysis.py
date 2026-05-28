"""
deep_analysis node — runs DeepAnalysisAgent (openai/gpt-oss-20b).

Conditional node — only runs when needs_deep_analysis=True.
Invoked for: critical finding counts > 5, financial exposure > $100K,
systemic patterns, or regulatory inquiry cases.

If skipped, the workflow routes directly from pattern_analysis → narrative_generation.
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


def deep_analysis(state: InvestigationState, config: RunnableConfig) -> dict:
    session     = config["configurable"]["session"]
    agent       = config["configurable"]["agents"]["deep_analysis"]
    run_id      = config["configurable"]["run_id"]
    case_memory = CaseMemory(case_id=state["case_id"], session=session)  # type: ignore[arg-type]

    wf_memory = config["configurable"].get("workflow_memory") or \
                WorkflowMemory(case_id=state["case_id"], run_id=run_id)

    logger.info(
        "Deep analysis triggered | case=%s | complexity=%s",
        state["case_id"],
        state.get("classification", {}).get("case_complexity", "unknown"),
    )

    update = agent.invoke(
        state, session, wf_memory, case_memory, workflow_step=5
    )

    merged_state = {**state, **update}
    _checkpoint_manager.save(
        session,
        case_id=state["case_id"],  # type: ignore[arg-type]
        run_id=run_id,
        agent_run_id=None,
        workflow_name="investigation",
        checkpoint_name="deep_analysis",
        state=merged_state,
    )

    return {**update, "current_node": "deep_analysis"}


def should_run_deep_analysis(state: InvestigationState) -> str:
    """
    Conditional edge function — routes based on needs_deep_analysis flag.
    Returns the name of the next node.
    """
    if state.get("needs_deep_analysis", False):
        logger.info("Case %s → deep_analysis (complex case)", state["case_id"])
        return "deep_analysis"
    logger.info("Case %s → narrative_generation (skipping deep analysis)", state["case_id"])
    return "narrative_generation"
