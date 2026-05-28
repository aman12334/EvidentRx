"""
classification node — runs ClassificationAgent (llama-3.1-8b-instant).

First LLM node in the workflow. Fast and cheap — assigns labels and sets
the needs_deep_analysis flag that controls the conditional deep_analysis branch.
"""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from agents.memory.case import CaseMemory
from agents.memory.workflow import WorkflowMemory
from agents.state import InvestigationState

logger = logging.getLogger(__name__)


def classification(state: InvestigationState, config: RunnableConfig) -> dict:
    session     = config["configurable"]["session"]
    agent       = config["configurable"]["agents"]["classification"]
    run_id      = config["configurable"]["run_id"]
    case_memory = CaseMemory(case_id=state["case_id"], session=session)  # type: ignore[arg-type]

    wf_memory = WorkflowMemory(case_id=state["case_id"], run_id=run_id)

    update = agent.invoke(
        state, session, wf_memory, case_memory, workflow_step=1
    )

    logger.info(
        "Classification complete | case=%s | category=%s | needs_deep=%s",
        state["case_id"],
        update.get("classification", {}).get("violation_category", "unknown"),
        update.get("needs_deep_analysis", False),
    )

    return {**update, "current_node": "classification"}
