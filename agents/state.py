"""
InvestigationState — the single TypedDict that flows through the entire
LangGraph investigation workflow.

Design rules:
  - All agent outputs are plain dicts (JSON-serializable) — no ORM objects.
  - Additive fields (errors, tokens) use operator.add reducer so nodes
    can return partial updates without clobbering each other.
  - No LLM message history — this is not a chatbot. Agents receive
    structured context built fresh from the DB state each time.
  - The state is checkpointed to audit.workflow_checkpoints after each node.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Optional
from typing_extensions import TypedDict


class InvestigationState(TypedDict):
    # ------------------------------------------------------------------
    # Identity (set once by runner, never mutated)
    # ------------------------------------------------------------------
    case_id: str
    run_id: str                          # UUID for this graph execution
    session_id: str                      # UUID grouping all traces in this run

    # ------------------------------------------------------------------
    # Case context (populated by case_intake node)
    # ------------------------------------------------------------------
    case: dict                           # InvestigationCase fields
    findings: list[dict]                 # audit_findings rows for this case
    evidence_summary: dict               # from EvidenceAggregationService
    risk_snapshot: dict                  # latest case_risk_snapshot

    # ------------------------------------------------------------------
    # Agent outputs (each node writes its own key, others remain)
    # ------------------------------------------------------------------
    evidence_analysis: dict              # EvidenceAnalysisAgent output
    risk_assessment: dict                # RiskPrioritizationAgent output
    patterns: list[dict]                 # identified cross-finding patterns
    narrative: dict                      # ComplianceNarrativeAgent output
    escalation_decision: dict            # escalation routing output
    case_summary: dict                   # final compiled summary

    # ------------------------------------------------------------------
    # New agent outputs
    # ------------------------------------------------------------------
    classification: dict                 # ClassificationAgent output (llama-3.1-8b-instant)
    deep_analysis: dict                  # DeepAnalysisAgent output (openai/gpt-oss-20b)
    orchestrator_plan: dict              # OrchestratorAgent LLM plan (groq/compound)

    # ------------------------------------------------------------------
    # Workflow control
    # ------------------------------------------------------------------
    current_node: str
    should_escalate: bool
    needs_deep_analysis: bool            # set by ClassificationAgent / RiskAgent
    escalation_route: str                # "hitl" | "analyst_queue" | "auto"
    is_complete: bool

    # Additive: each node appends its errors without replacing prior ones
    errors: Annotated[list[dict], operator.add]

    # ------------------------------------------------------------------
    # Token accounting — additive across all nodes
    # ------------------------------------------------------------------
    total_input_tokens: Annotated[int, operator.add]
    total_output_tokens: Annotated[int, operator.add]
    total_cache_read_tokens: Annotated[int, operator.add]

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------
    started_at: str
    completed_at: Optional[str]


def initial_state(case_id: str, run_id: str, session_id: str) -> InvestigationState:
    """Returns the minimal valid starting state before case_intake runs."""
    return InvestigationState(
        case_id=case_id,
        run_id=run_id,
        session_id=session_id,
        case={},
        findings=[],
        evidence_summary={},
        risk_snapshot={},
        evidence_analysis={},
        risk_assessment={},
        patterns=[],
        narrative={},
        escalation_decision={},
        case_summary={},
        classification={},
        deep_analysis={},
        orchestrator_plan={},
        current_node="case_intake",
        should_escalate=False,
        needs_deep_analysis=False,
        escalation_route="auto",
        is_complete=False,
        errors=[],
        total_input_tokens=0,
        total_output_tokens=0,
        total_cache_read_tokens=0,
        started_at="",
        completed_at=None,
    )
