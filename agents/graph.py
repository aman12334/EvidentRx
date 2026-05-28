"""
Investigation Workflow Graph — LangGraph StateGraph definition.

Topology:
    case_intake
        ↓
    classification              ← llama-3.1-8b-instant (fast labels)
        ↓
    evidence_aggregation        ← openai/gpt-oss-120b (pattern detection)
        ↓
    risk_prioritization         ← qwen/qwen3-32b (exposure scoring)
        ↓
    pattern_analysis            ← openai/gpt-oss-20b (deep reasoning)
        ↓ [conditional: needs_deep_analysis?]
        ├── YES → deep_analysis ← openai/gpt-oss-20b (adversarial review)
        │            ↓
        └── NO  → narrative_generation
                    ↓
    narrative_generation        ← llama-3.3-70b-versatile (HRSA prose)
        ↓
    escalation_decision         ← pure logic (Orchestrator)
        ↓ [conditional: escalation_route]
        ├── hitl          → human_review  (critical — HITL gate)
        ├── analyst_queue → analyst_queue (high — senior analyst)
        └── auto          → case_summary  (medium/low — automated)
                                ↓
                              END

Design rules:
  - Each node is a pure function accepting (state, config)
  - Nodes never import from each other (only from agents/ and services/)
  - The graph is compiled once at module level and reused across runs
  - MemorySaver provides within-run state management (ephemeral)
  - Durable checkpoints go to audit.workflow_checkpoints (our DB layer)
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.graph import CompiledGraph

from agents.nodes.case_intake import case_intake
from agents.nodes.case_summary import case_summary
from agents.nodes.classification import classification
from agents.nodes.deep_analysis import deep_analysis, should_run_deep_analysis
from agents.nodes.escalation_decision import escalation_decision, route_after_escalation
from agents.nodes.evidence_aggregation import evidence_aggregation
from agents.nodes.narrative_generation import narrative_generation
from agents.nodes.pattern_analysis import pattern_analysis
from agents.nodes.risk_prioritization import risk_prioritization
from agents.state import InvestigationState


def _human_review(state: InvestigationState, config) -> dict:
    """
    HITL placeholder — flags the case for mandatory human review.
    In production this would pause the workflow and notify an analyst.
    For now: records the flag and routes to case_summary.
    """
    return {
        "current_node": "human_review",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read_tokens": 0,
    }


def _analyst_queue(state: InvestigationState, config) -> dict:
    """
    Analyst queue placeholder — queues case for senior analyst review.
    """
    return {
        "current_node": "analyst_queue",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read_tokens": 0,
    }


def build_investigation_graph() -> CompiledGraph:
    """
    Constructs and compiles the investigation workflow graph.
    Returns a compiled LangGraph app ready for .invoke() calls.
    """
    workflow = StateGraph(InvestigationState)

    # ── Register all nodes ────────────────────────────────────────────────────
    workflow.add_node("case_intake",          case_intake)
    workflow.add_node("classification",       classification)
    workflow.add_node("evidence_aggregation", evidence_aggregation)
    workflow.add_node("risk_prioritization",  risk_prioritization)
    workflow.add_node("pattern_analysis",     pattern_analysis)
    workflow.add_node("deep_analysis",        deep_analysis)
    workflow.add_node("narrative_generation", narrative_generation)
    workflow.add_node("escalation_decision",  escalation_decision)
    workflow.add_node("human_review",         _human_review)
    workflow.add_node("analyst_queue",        _analyst_queue)
    workflow.add_node("case_summary",         case_summary)

    # ── Linear edges ──────────────────────────────────────────────────────────
    workflow.set_entry_point("case_intake")
    workflow.add_edge("case_intake",          "classification")
    workflow.add_edge("classification",       "evidence_aggregation")
    workflow.add_edge("evidence_aggregation", "risk_prioritization")
    workflow.add_edge("risk_prioritization",  "pattern_analysis")

    # ── Conditional: deep analysis branch ────────────────────────────────────
    workflow.add_conditional_edges(
        "pattern_analysis",
        should_run_deep_analysis,
        {
            "deep_analysis":      "deep_analysis",
            "narrative_generation": "narrative_generation",
        },
    )
    workflow.add_edge("deep_analysis", "narrative_generation")

    # ── Narrative → escalation ────────────────────────────────────────────────
    workflow.add_edge("narrative_generation", "escalation_decision")

    # ── Conditional: escalation gate ─────────────────────────────────────────
    workflow.add_conditional_edges(
        "escalation_decision",
        route_after_escalation,
        {
            "human_review":  "human_review",
            "analyst_queue": "analyst_queue",
            "case_summary":  "case_summary",
        },
    )

    # ── Terminal edges ────────────────────────────────────────────────────────
    workflow.add_edge("human_review",  "case_summary")
    workflow.add_edge("analyst_queue", "case_summary")
    workflow.add_edge("case_summary",  END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)


# Module-level compiled graph — build once, reuse across all runs
_GRAPH: CompiledGraph | None = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_investigation_graph()
    return _GRAPH
