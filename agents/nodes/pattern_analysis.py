"""
pattern_analysis node — extracts and enriches patterns from evidence_analysis.
Pure computation — no LLM call. Promotes patterns to state for the narrative agent.
"""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from agents.state import InvestigationState

logger = logging.getLogger(__name__)


def pattern_analysis(state: InvestigationState, config: RunnableConfig) -> dict:
    """
    Derives structured pattern objects from evidence_analysis output.
    Enriches patterns with severity and financial context.
    """
    evidence = state.get("evidence_analysis", {})
    snap = state.get("risk_snapshot", {})

    anomalies = evidence.get("recurring_anomalies", [])

    # Enrich with financial context
    total_exposure = snap.get("total_financial_exposure") or 0
    total_findings = snap.get("total_findings", 1) or 1

    enriched_patterns = []
    for anomaly in anomalies:
        count = anomaly.get("finding_count", 1)
        enriched_patterns.append({
            **anomaly,
            "proportion_of_findings": round(count / total_findings, 3),
            "estimated_exposure_share": (
                round((count / total_findings) * total_exposure, 2)
                if total_exposure else None
            ),
        })

    # Add meta-pattern: systemic vs isolated
    systemic = evidence.get("systemic_vs_isolated", "unclear")
    if systemic == "systemic":
        enriched_patterns.insert(0, {
            "anomaly": f"Systemic pattern detected — {evidence.get('pattern_summary', '')}",
            "finding_count": total_findings,
            "significance": "high",
            "pattern_type": "systemic",
            "proportion_of_findings": 1.0,
        })

    logger.info(
        "pattern_analysis: %d patterns for case %s",
        len(enriched_patterns), state["case_id"],
    )

    return {
        "patterns":     enriched_patterns,
        "current_node": "pattern_analysis",
        "total_input_tokens":      0,
        "total_output_tokens":     0,
        "total_cache_read_tokens": 0,
    }
