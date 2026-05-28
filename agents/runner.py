"""
InvestigationRunner — the single entry point for running the investigation workflow.

Usage:
    runner = InvestigationRunner.from_env()
    result = runner.run(session, case_id)

The runner:
  1. Builds the initial InvestigationState
  2. Wires up all agents, memory, and persistence into the LangGraph config
  3. Invokes the compiled graph
  4. Returns the final state as a summary dict

Error handling:
  - LangGraph errors surface as exceptions (runner lets them propagate)
  - Node-level errors are captured in state["errors"] and do not abort the workflow
  - If case_intake fails fatally, the runner raises ValueError
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from agents.agents.evidence_analysis import EvidenceAnalysisAgent
from agents.agents.narrative import ComplianceNarrativeAgent
from agents.agents.orchestrator import InvestigationOrchestratorAgent
from agents.agents.risk_prioritization import RiskPrioritizationAgent
from agents.graph import get_graph
from agents.llm.router import ModelRouter, build_router_from_env
from agents.state import InvestigationState, initial_state

logger = logging.getLogger(__name__)


class InvestigationRunner:
    """
    Coordinates a single investigation workflow run for one case.
    """

    WORKFLOW_NAME = "investigation"

    def __init__(self, router: ModelRouter) -> None:
        self._router     = router
        self._orchestrator = InvestigationOrchestratorAgent()
        self._agents = {
            "evidence_analysis":  EvidenceAnalysisAgent(router),
            "risk_prioritization": RiskPrioritizationAgent(router),
            "narrative":           ComplianceNarrativeAgent(router),
        }

    @classmethod
    def from_env(cls) -> InvestigationRunner:
        """Builds a runner from environment variable API keys."""
        router = build_router_from_env()
        return cls(router=router)

    def run(
        self,
        session: Session,
        case_id: UUID,
        thread_id: str | None = None,
    ) -> dict:
        """
        Runs the full investigation workflow for a case.
        Returns a summary dict of the final state.
        """
        run_id     = str(uuid4())
        session_id = str(uuid4())
        t_id       = thread_id or run_id

        logger.info("Starting investigation run=%s case=%s", run_id, case_id)

        state = initial_state(
            case_id=str(case_id),
            run_id=run_id,
            session_id=session_id,
        )
        state["started_at"] = datetime.now(UTC).isoformat()

        config = {
            "configurable": {
                "thread_id":  t_id,
                "session":    session,
                "run_id":     run_id,
                "orchestrator": self._orchestrator,
                "agents":     self._agents,
            }
        }

        graph = get_graph()
        final_state: InvestigationState = graph.invoke(state, config=config)

        session.commit()

        return self._build_run_result(final_state)

    def resume(
        self,
        session: Session,
        case_id: UUID,
        checkpoint_name: str,
    ) -> dict:
        """
        Resumes a paused workflow from the latest resumable DB checkpoint.
        Loads saved state and re-invokes the graph from the checkpoint node.
        """
        from agents.persistence.checkpoints import CheckpointManager
        cm = CheckpointManager()

        checkpoint = cm.load_latest(session, case_id, self.WORKFLOW_NAME)
        if not checkpoint:
            raise ValueError(
                f"No resumable checkpoint found for case {case_id} in workflow '{self.WORKFLOW_NAME}'"
            )

        logger.info(
            "Resuming case=%s from checkpoint=%s",
            case_id, checkpoint["checkpoint_name"],
        )

        # Re-run with saved state (LangGraph will re-execute from current node)
        saved_state: InvestigationState = checkpoint["state"]
        run_id     = str(uuid4())
        session_id = str(uuid4())

        saved_state["run_id"]     = run_id
        saved_state["session_id"] = session_id

        config = {
            "configurable": {
                "thread_id":    run_id,
                "session":      session,
                "run_id":       run_id,
                "orchestrator": self._orchestrator,
                "agents":       self._agents,
            }
        }

        graph = get_graph()
        final_state = graph.invoke(saved_state, config=config)
        session.commit()

        return self._build_run_result(final_state)

    def _build_run_result(self, state: InvestigationState) -> dict:
        return {
            "case_id":             state["case_id"],
            "run_id":              state["run_id"],
            "is_complete":         state.get("is_complete", False),
            "escalated":           state.get("should_escalate", False),
            "errors":              state.get("errors", []),
            "total_input_tokens":  state.get("total_input_tokens", 0),
            "total_output_tokens": state.get("total_output_tokens", 0),
            "cache_read_tokens":   state.get("total_cache_read_tokens", 0),
            "risk_level":          state.get("risk_assessment", {}).get("overall_risk_level"),
            "executive_summary":   state.get("narrative", {}).get("executive_summary", ""),
            "started_at":          state.get("started_at"),
            "completed_at":        state.get("completed_at"),
        }
