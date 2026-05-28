"""
InvestigationOrchestratorAgent — coordinates agent execution and workflow routing.

Now makes an LLM call via groq/compound at workflow start to:
  - Produce an investigation plan
  - Identify which agents need to run
  - Set priorities for downstream agents

Also provides pure-logic routing decisions used by conditional graph edges.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from agents.llm.base import Message
from agents.llm.router import ModelRouter
from agents.state import InvestigationState

logger = logging.getLogger(__name__)

_ORCHESTRATOR_SYSTEM = """You are an investigation orchestrator for a 340B pharmaceutical compliance platform.
Your job is to produce a structured investigation plan for the agents that will run next.

Output ONLY valid JSON:
{
  "investigation_focus": "<one sentence — what this investigation is primarily about>",
  "priority_agents": ["evidence_analysis", "risk_prioritization", "classification"],
  "key_questions": ["<question1>", "<question2>", "<question3>"],
  "data_focus_areas": ["<area1>", "<area2>"],
  "escalation_threshold": "critical|high|medium",
  "estimated_complexity": "simple|moderate|complex"
}"""

_ORCHESTRATOR_USER = """Plan the investigation for case {case_id}.

case_type: {case_type}
priority: {priority}
total_findings: {total_findings}
critical_findings: {critical_findings}
financial_exposure: {financial_exposure}
risk_score: {risk_score}"""


class InvestigationOrchestratorAgent:
    """
    Orchestration coordinator — uses groq/compound for planning,
    plus pure-logic routing decisions for conditional graph edges.
    """

    def __init__(self, router: ModelRouter | None = None) -> None:
        self._router = router

    def plan(self, state: InvestigationState) -> dict:
        """
        LLM call via groq/compound — produces an investigation plan.
        Returns a dict written to state["orchestrator_plan"].
        Falls back to a default plan if the LLM is unavailable.
        """
        if self._router is None:
            return self._default_plan(state)

        case = state.get("case", {})
        snap = state.get("risk_snapshot", {})

        messages = [
            Message(role="system", content=_ORCHESTRATOR_SYSTEM),
            Message(role="user", content=_ORCHESTRATOR_USER.format(
                case_id=state["case_id"],
                case_type=case.get("case_type", "unknown"),
                priority=case.get("priority", "medium"),
                total_findings=snap.get("total_findings", 0),
                critical_findings=snap.get("critical_findings", 0),
                financial_exposure=snap.get("total_financial_exposure", 0),
                risk_score=snap.get("composite_risk_score", 0),
            )),
        ]

        try:
            response = self._router.route(
                "orchestration",
                messages,
                override_max_tokens=1024,
            )
            return response.structured or self._default_plan(state)
        except Exception as e:
            logger.warning("Orchestrator LLM call failed: %s — using default plan", e)
            return self._default_plan(state)

    def _default_plan(self, state: InvestigationState) -> dict:
        snap = state.get("risk_snapshot", {})
        critical = snap.get("critical_findings", 0)
        exposure = float(snap.get("total_financial_exposure") or 0)
        return {
            "investigation_focus": "Standard 340B compliance investigation",
            "priority_agents": ["classification", "evidence_analysis", "risk_prioritization"],
            "key_questions": ["What is the primary violation pattern?", "Is this systemic?"],
            "data_focus_areas": ["finding_distribution", "financial_exposure"],
            "escalation_threshold": "critical" if critical > 5 or exposure > 100_000 else "high",
            "estimated_complexity": "complex" if critical > 10 else "moderate",
        }

    def validate_case_ready(
        self, state: InvestigationState, session: Session
    ) -> tuple[bool, list[str]]:
        """
        Validates that a case has sufficient data to run the investigation workflow.
        Returns (is_ready, list_of_blocking_reasons).
        """
        reasons: list[str] = []

        if not state.get("case"):
            reasons.append("Case metadata not loaded")

        if not state.get("findings"):
            reasons.append("No findings linked to this case")

        if not state.get("risk_snapshot"):
            reasons.append("No risk snapshot available — run EvidenceAggregationService first")

        snap = state.get("risk_snapshot", {})
        if snap.get("total_findings", 0) == 0:
            reasons.append("Risk snapshot shows 0 findings")

        return len(reasons) == 0, reasons

    def should_escalate(self, state: InvestigationState) -> bool:
        """
        Determines escalation based on accumulated agent outputs.
        This is the decision point for the escalation_decision conditional edge.
        """
        # Direct state flag set by RiskPrioritizationAgent
        if state.get("should_escalate"):
            return True

        # Override: always escalate critical cases with significant exposure
        snap = state.get("risk_snapshot", {})
        by_sev = snap.get("by_severity", {})
        _exposure = snap.get("total_financial_exposure") or 0  # reserved for future threshold

        if by_sev.get("critical", 0) >= 10:
            return True

        risk = state.get("risk_assessment", {})
        if risk.get("overall_risk_level") == "critical":
            return True

        return False

    def get_workflow_summary(self, state: InvestigationState) -> dict:
        """
        Builds a final workflow execution summary for the case_summary node.
        """
        snap = state.get("risk_snapshot", {})
        return {
            "run_id":                state.get("run_id"),
            "case_id":               state.get("case_id"),
            "workflow_completed":    True,
            "nodes_executed":        self._infer_nodes_executed(state),
            "escalated":             state.get("should_escalate", False),
            "errors_encountered":    len(state.get("errors", [])),
            "total_input_tokens":    state.get("total_input_tokens", 0),
            "total_output_tokens":   state.get("total_output_tokens", 0),
            "cache_read_tokens":     state.get("total_cache_read_tokens", 0),
            "findings_analyzed":     snap.get("total_findings", 0),
            "risk_level":            state.get("risk_assessment", {}).get("overall_risk_level"),
        }

    def _infer_nodes_executed(self, state: InvestigationState) -> list[str]:
        executed = ["case_intake", "evidence_aggregation"]
        if state.get("evidence_analysis"):
            executed.append("pattern_analysis")
        if state.get("risk_assessment"):
            executed.append("risk_prioritization")
        if state.get("narrative"):
            executed.append("narrative_generation")
        executed.append("escalation_decision")
        executed.append("case_summary")
        return executed
