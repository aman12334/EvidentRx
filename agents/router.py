"""
AgentRouter — entry point for the multi-agent investigation system.

Responsibilities:
  1. Receive a case_id trigger
  2. Validate the case exists and has sufficient data
  3. Build the agent registry (one instance per agent type, each with its model)
  4. Dispatch to the appropriate workflow graph
  5. Return the completed workflow result

Model assignment per agent (all via Groq free tier):
  ┌──────────────────────┬──────────────────────────────┐
  │ Agent                │ Model                        │
  ├──────────────────────┼──────────────────────────────┤
  │ Orchestrator         │ groq/compound                │
  │ Classification       │ llama-3.1-8b-instant         │
  │ Evidence Analysis    │ openai/gpt-oss-120b          │
  │ Risk Prioritization  │ qwen/qwen3-32b               │
  │ Deep Analysis        │ openai/gpt-oss-20b           │
  │ Narrative Generation │ llama-3.3-70b-versatile      │
  └──────────────────────┴──────────────────────────────┘
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from agents.agents.classification_agent import ClassificationAgent
from agents.agents.deep_analysis_agent import DeepAnalysisAgent
from agents.agents.evidence_analysis import EvidenceAnalysisAgent
from agents.agents.narrative import ComplianceNarrativeAgent
from agents.agents.orchestrator import InvestigationOrchestratorAgent
from agents.agents.risk_prioritization import RiskPrioritizationAgent
from agents.graph import get_graph
from agents.llm.router import ModelRouter, get_llm_router
from agents.state import initial_state

logger = logging.getLogger(__name__)


class AgentRouter:
    """
    Top-level dispatcher for the multi-agent investigation workflow.

    Usage:
        router = AgentRouter()
        result = router.dispatch(case_id="...", session=db_session)
    """

    def __init__(self, llm_router: ModelRouter | None = None) -> None:
        self._llm_router = llm_router or get_llm_router()
        self._graph = get_graph()
        self._agents = self._build_agent_registry()
        self._orchestrator = InvestigationOrchestratorAgent(router=self._llm_router)
        logger.info(
            "AgentRouter initialized | agents=%s",
            list(self._agents.keys()),
        )

    def _build_agent_registry(self) -> dict:
        """
        Instantiate one agent per type. Each agent shares the same
        ModelRouter but declares its own task_type → model mapping.
        """
        return {
            "classification":    ClassificationAgent(router=self._llm_router),
            "evidence_analysis": EvidenceAnalysisAgent(router=self._llm_router),
            "risk_prioritization": RiskPrioritizationAgent(router=self._llm_router),
            "deep_analysis":     DeepAnalysisAgent(router=self._llm_router),
            "narrative_generation": ComplianceNarrativeAgent(router=self._llm_router),
        }

    def dispatch(
        self,
        case_id: str | UUID,
        session: Session,
    ) -> dict:
        """
        Route a case through the full multi-agent investigation workflow.

        Flow:
          AgentRouter
            → Orchestrator.plan()          (groq/compound — LLM)
            → LangGraph workflow:
                case_intake
                → classification           (llama-3.1-8b-instant)
                → evidence_aggregation     (openai/gpt-oss-120b)
                → risk_prioritization      (qwen/qwen3-32b)
                → pattern_analysis         (openai/gpt-oss-20b)
                → [conditional]
                    needs_deep → deep_analysis (openai/gpt-oss-20b)
                    skip       → narrative_generation
                → narrative_generation     (llama-3.3-70b-versatile)
                → escalation_decision      (pure logic)
                    → hitl / analyst_queue / case_summary
        """
        case_id_str = str(case_id)
        run_id      = str(uuid.uuid4())
        session_id  = str(uuid.uuid4())

        logger.info(
            "AgentRouter.dispatch | case=%s run=%s", case_id_str, run_id
        )

        # Build initial state
        state = initial_state(
            case_id=case_id_str,
            run_id=run_id,
            session_id=session_id,
        )
        state["started_at"] = datetime.now(tz=UTC).isoformat()

        # ── Step 1: Orchestrator planning (LLM call) ──────────────────────────
        try:
            # Load minimal case context for orchestrator planning
            from sqlalchemy import text
            row = session.execute(
                text("""
                    SELECT ic.case_type, ic.status, ic.priority,
                           crs.total_findings, crs.critical_findings,
                           crs.total_financial_exposure, crs.composite_risk_score
                    FROM audit.investigation_cases ic
                    LEFT JOIN LATERAL (
                        SELECT total_findings, critical_findings,
                               total_financial_exposure, composite_risk_score
                        FROM audit.case_risk_snapshots
                        WHERE case_id = ic.case_id
                        ORDER BY snapshot_at DESC LIMIT 1
                    ) crs ON TRUE
                    WHERE ic.case_id = CAST(:cid AS uuid)
                """),
                {"cid": case_id_str},
            ).mappings().fetchone()

            if row:
                state["case"] = dict(row)
                state["risk_snapshot"] = {
                    "total_findings":         row["total_findings"] or 0,
                    "critical_findings":      row["critical_findings"] or 0,
                    "total_financial_exposure": float(row["total_financial_exposure"] or 0),
                    "composite_risk_score":   float(row["composite_risk_score"] or 0),
                }

            plan = self._orchestrator.plan(state)
            state["orchestrator_plan"] = plan
            logger.info(
                "Orchestrator plan | complexity=%s | escalation_threshold=%s",
                plan.get("estimated_complexity"),
                plan.get("escalation_threshold"),
            )
        except Exception as e:
            logger.warning("Orchestrator planning failed: %s — continuing", e)

        # ── Step 2: Dispatch to LangGraph workflow ────────────────────────────
        graph_config = {
            "configurable": {
                "session":      session,
                "agents":       self._agents,
                "orchestrator": self._orchestrator,
                "run_id":       run_id,
                "thread_id":    run_id,
            }
        }

        try:
            result = self._graph.invoke(state, config=graph_config)
            result["completed_at"] = datetime.now(tz=UTC).isoformat()
            logger.info(
                "Workflow complete | case=%s | escalation_route=%s | tokens_in=%d tokens_out=%d",
                case_id_str,
                result.get("escalation_route", "auto"),
                result.get("total_input_tokens", 0),
                result.get("total_output_tokens", 0),
            )
            return result
        except Exception:
            logger.exception("Workflow failed for case %s", case_id_str)
            raise


# Module-level singleton
_agent_router: AgentRouter | None = None


def get_agent_router() -> AgentRouter:
    """Return the shared AgentRouter singleton."""
    global _agent_router
    if _agent_router is None:
        _agent_router = AgentRouter()
    return _agent_router
