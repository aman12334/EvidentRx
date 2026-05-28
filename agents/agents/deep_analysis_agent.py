"""
DeepAnalysisAgent — chain-of-thought reasoning for complex or escalated cases.

Only invoked when needs_deep_analysis=True (set by ClassificationAgent or
RiskPrioritizationAgent). Uses openai/gpt-oss-20b on Groq for deeper
multi-hop reasoning.

Responsibilities:
  - Multi-hop pattern detection across findings and entity relationships
  - Adversarial review — challenge the evidence for audit defensibility
  - Resolve ambiguous or conflicting signals from prior agents
  - Produce actionable recommendations for the Narrative Agent downstream

This agent does NOT write violations or override the rules engine.
"""
from __future__ import annotations

import json

from agents.agents.base import BaseAgent
from agents.llm.base import LLMResponse, Message
from agents.memory.case import CaseMemory
from agents.memory.workflow import WorkflowMemory
from agents.roles import AgentRole
from agents.state import InvestigationState

_ROLE = AgentRole(
    title="340B Deep Compliance Analyst",
    mandate=(
        "Perform adversarial, multi-hop reasoning on complex 340B investigation cases. "
        "You receive outputs from the Evidence and Risk agents and must challenge, "
        "validate, and extend their findings. Surface patterns they may have missed. "
        "Flag audit defensibility risks. Resolve ambiguous signals."
    ),
    authorities=[
        "Challenge evidence agent conclusions with alternative explanations",
        "Identify multi-hop patterns spanning multiple findings or pharmacies",
        "Assess whether the evidence package would survive HRSA audit scrutiny",
        "Recommend additional data points that would strengthen the case",
        "Flag conflicting signals between risk score and finding distribution",
    ],
    prohibitions=[
        "Creating new violation findings not present in the confirmed data",
        "Overriding severity levels set by the deterministic rules engine",
        "Making legal determinations about liability or penalty exposure",
        "Producing prose outside the required JSON structure",
    ],
    output_contract=[
        "`adversarial_review` — challenges to the evidence agent's conclusions",
        "`multi_hop_patterns` — patterns spanning multiple entities or findings",
        "`audit_readiness` — assessment of HRSA audit defensibility",
        "`unresolved_signals` — conflicting data that needs human review",
        "`recommended_data_requests` — additional evidence to collect",
        "`confidence_score` — float 0.0-1.0",
    ],
)

_SYSTEM = """{role_block}

You are reviewing a 340B compliance investigation that has been flagged as complex.
Think step-by-step. Surface what the other agents may have missed.

Output ONLY valid JSON:
{{
  "adversarial_review": "<challenge or validate the evidence agent conclusions>",
  "multi_hop_patterns": [
    {{"pattern": "<description>", "entities_involved": ["<entity1>"], "severity": "high|medium|low"}}
  ],
  "audit_readiness": {{
    "score": <float 0.0-1.0>,
    "gaps": ["<gap1>", "<gap2>"],
    "strengths": ["<strength1>"]
  }},
  "unresolved_signals": ["<signal1>", "<signal2>"],
  "recommended_data_requests": ["<request1>", "<request2>"],
  "revised_risk_assessment": "escalate|maintain|downgrade",
  "revised_risk_rationale": "<why>",
  "confidence_score": <float 0.0-1.0>
}}"""

_USER = """Deep analysis required for case {case_id}.

CLASSIFICATION:
{classification}

EVIDENCE ANALYSIS OUTPUT:
{evidence_analysis}

RISK ASSESSMENT OUTPUT:
{risk_assessment}

RISK SNAPSHOT:
{risk_snapshot}

Perform adversarial review and surface what was missed."""


class DeepAnalysisAgent(BaseAgent):
    agent_type = "deep_analysis"
    task_type  = "pattern_analysis"       # → openai/gpt-oss-20b

    def _build_messages(
        self,
        state: InvestigationState,
        case_memory: CaseMemory,
        workflow_memory: WorkflowMemory,
    ) -> list[Message]:
        system_content = _SYSTEM.format(role_block=_ROLE.to_system_block())

        user_content = _USER.format(
            case_id=state["case_id"],
            classification=json.dumps(state.get("classification", {}), default=str),
            evidence_analysis=json.dumps(state.get("evidence_analysis", {}), default=str),
            risk_assessment=json.dumps(state.get("risk_assessment", {}), default=str),
            risk_snapshot=json.dumps(state.get("risk_snapshot", {}), default=str),
        )

        return [
            Message(role="system", content=system_content),
            Message(role="user",   content=user_content),
        ]

    def _parse_response(self, response: LLMResponse) -> tuple[dict, float | None]:
        structured = response.structured or {}
        confidence = float(structured.get("confidence_score", 0.75))
        return structured, confidence

    def _state_update_key(self, output: dict) -> dict:
        # If deep analysis recommends escalation, set the flag
        revised = output.get("revised_risk_assessment", "maintain")
        return {
            "deep_analysis":  output,
            "should_escalate": revised == "escalate",
        }
