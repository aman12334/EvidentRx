"""
RiskPrioritizationAgent — ranks investigation priority, assesses financial
exposure, and recommends escalation based on confirmed evidence.

Does NOT determine whether violations occurred — only prioritizes among
violations the rules engine already confirmed. Escalation recommendation
is advisory — a human compliance officer makes the final decision.
"""
from __future__ import annotations

import json
from typing import ClassVar

from agents.agents.base import BaseAgent
from agents.llm.base import LLMResponse, Message
from agents.memory.case import CaseMemory
from agents.memory.workflow import WorkflowMemory
from agents.roles import RISK_ASSESSOR, AgentRole
from agents.schemas.inputs import RiskPrioritizationInput
from agents.state import InvestigationState

_DOMAIN_INSTRUCTIONS = """You are operating within an enterprise 340B pharmaceutical compliance platform.

The deterministic rules engine has already confirmed the violations. Your job is to assess
severity, financial exposure, and escalation priority — not to re-determine whether violations occurred.

Escalation recommendations are ADVISORY. A human compliance officer reviews all escalation decisions.

Output ONLY valid JSON in the exact schema specified.

Respond with this exact JSON structure:
{
  "overall_risk_level": "critical|high|medium|low",
  "priority_rank": <int 1-5, 1=highest priority>,
  "escalation_recommended": <bool>,
  "escalation_rationale": "<why this case warrants escalation or not — required regardless of recommendation>",
  "financial_exposure_assessment": {
    "minimum_estimate_usd": <float>,
    "maximum_estimate_usd": <float>,
    "methodology": "<how you derived this range — required>"
  },
  "regulatory_risk": {
    "audit_likelihood": "high|medium|low",
    "enforcement_risk": "high|medium|low",
    "key_regulatory_concerns": ["<concern 1>", "<concern 2>"]
  },
  "remediation_urgency": "immediate|within_30_days|within_90_days|routine",
  "resource_allocation_recommendation": "<how much investigator time this warrants>",
  "confidence_score": <float 0.0-1.0>
}"""

_USER_TEMPLATE = """Assess risk and prioritization for 340B investigation case {case_id}.

CASE CONTEXT:
{case_context}

EVIDENCE ANALYSIS (from EvidenceAnalysisAgent):
{evidence_analysis}

RISK SNAPSHOT:
- Total findings: {total_findings}
- Critical: {critical} | High: {high} | Medium: {medium} | Low: {low}
- Total financial exposure: ${exposure}
- Temporal window: {window_start} to {window_end}
- Unique NDCs affected: {ndc_count}
- Unique patients affected: {patient_count}

FINDINGS BY RULE:
{findings_by_rule}"""


class RiskPrioritizationAgent(BaseAgent):
    agent_type = "risk_prioritization"
    task_type  = "risk_prioritization"
    role: ClassVar[AgentRole] = RISK_ASSESSOR
    input_schema = RiskPrioritizationInput

    def _extract_input(self, state: InvestigationState) -> dict:
        return {
            "case_id":          state["case_id"],
            "risk_snapshot":    state.get("risk_snapshot", {}),
            "evidence_analysis": state.get("evidence_analysis", {}),
        }

    def _build_messages(
        self,
        state: InvestigationState,
        case_memory: CaseMemory,
        workflow_memory: WorkflowMemory,
    ) -> list[Message]:
        snap   = state.get("risk_snapshot", {})
        by_sev = snap.get("by_severity", {})

        user_content = _USER_TEMPLATE.format(
            case_id=state["case_id"],
            case_context=json.dumps(state.get("case", {}), default=str),
            evidence_analysis=json.dumps(state.get("evidence_analysis", {}), default=str),
            total_findings=snap.get("total_findings", 0),
            critical=by_sev.get("critical", 0),
            high=by_sev.get("high", 0),
            medium=by_sev.get("medium", 0),
            low=by_sev.get("low", 0),
            exposure=snap.get("total_financial_exposure") or "unknown",
            window_start=snap.get("temporal_window", {}).get("start", "unknown"),
            window_end=snap.get("temporal_window", {}).get("end", "unknown"),
            ndc_count=len(snap.get("ndc_list", [])),
            patient_count=snap.get("unique_patients", 0),
            findings_by_rule=json.dumps(snap.get("findings_by_rule", {}), indent=2),
        )

        system_content = self.role.to_system_block() + _DOMAIN_INSTRUCTIONS

        return [
            Message(role="system", content=system_content),
            Message(role="user",   content=user_content),
        ]

    def _parse_response(self, response: LLMResponse) -> tuple[dict, float | None]:
        structured = response.structured or {}
        confidence = float(structured.get("confidence_score", 0.85))
        return structured, confidence

    def _state_update_key(self, output: dict) -> dict:
        return {
            "risk_assessment": output,
            "should_escalate": bool(output.get("escalation_recommended", False)),
        }
