"""
ComplianceNarrativeAgent — generates audit-ready documentation from
confirmed findings and prior agent analysis.

Produces:
  - Executive summary (C-suite readable)
  - Technical findings narrative (HRSA auditor readable)
  - Regulatory context with statute citations
  - Remediation recommendations
  - Audit preparation notes

This agent never invents findings. It translates confirmed violations
and risk assessments into plain English documentation. It is the final
agent in the pipeline — its output is what gets filed.
"""
from __future__ import annotations

import json
from typing import ClassVar, Optional

from agents.agents.base import BaseAgent
from agents.llm.base import LLMResponse, Message
from agents.memory.case import CaseMemory
from agents.memory.workflow import WorkflowMemory
from agents.roles import NARRATIVE_SPECIALIST, AgentRole
from agents.schemas.inputs import NarrativeGenerationInput
from agents.state import InvestigationState

_DOMAIN_INSTRUCTIONS = """You are operating within an enterprise 340B pharmaceutical compliance platform.

Write for two audiences simultaneously:
  - Executive summary: readable by hospital CFO, VP of Pharmacy, and Chief Compliance Officer
  - Technical narrative: readable by HRSA auditors and external compliance counsel

Every factual claim must be traceable to the confirmed findings provided. Do not infer additional
violations. Cite specific 340B statute sections (e.g. 42 U.S.C. § 256b) or HRSA guidance documents
(e.g. HRSA 340B Program Omnibus Guidance) by name in the regulatory_context field.

Output ONLY valid JSON. All text fields must be complete, professional prose.

Respond with this exact JSON structure:
{
  "executive_summary": "<minimum 2 paragraphs — what happened, why it matters, what leadership must do>",
  "technical_findings": "<3-5 paragraphs — specific violations, rule codes referenced, operational context>",
  "regulatory_context": "<which 340B statutes and HRSA guidance apply and why these findings are violations — cite sections>",
  "financial_impact_summary": "<explanation of the exposure range and estimation methodology>",
  "remediation_recommendations": [
    {
      "priority": "immediate|short_term|long_term",
      "action": "<specific action to take>",
      "rationale": "<why this action addresses the root cause>"
    }
  ],
  "audit_preparation_notes": "<what documentation the CE should gather if HRSA requests an audit>",
  "confidence_score": <float 0.0-1.0>
}"""

_USER_TEMPLATE = """Generate compliance documentation for 340B investigation case {case_id}.

COVERED ENTITY:
{case_context}

INVESTIGATION FINDINGS SUMMARY:
- Total violations confirmed: {total_findings}
- Severity breakdown: {severity_breakdown}
- Rules triggered: {rules_triggered}
- Investigation period: {window_start} to {window_end}

PATTERN ANALYSIS (from EvidenceAnalysisAgent):
{pattern_summary}

RISK ASSESSMENT (from RiskPrioritizationAgent):
- Overall risk level: {risk_level}
- Financial exposure estimate: ${exposure_min} – ${exposure_max}
- Regulatory risk: {regulatory_risk}
- Remediation urgency: {remediation_urgency}
- Escalation recommended: {escalation_recommended}
- Escalation rationale: {escalation_rationale}

RECURRING ANOMALIES:
{anomalies}"""


class ComplianceNarrativeAgent(BaseAgent):
    agent_type = "narrative_generation"
    task_type  = "narrative_generation"
    role: ClassVar[AgentRole] = NARRATIVE_SPECIALIST
    input_schema = NarrativeGenerationInput

    def _extract_input(self, state: InvestigationState) -> dict:
        return {
            "case_id":          state["case_id"],
            "risk_snapshot":    state.get("risk_snapshot", {}),
            "evidence_analysis": state.get("evidence_analysis", {}),
            "risk_assessment":  state.get("risk_assessment", {}),
        }

    def _build_messages(
        self,
        state: InvestigationState,
        case_memory: CaseMemory,
        workflow_memory: WorkflowMemory,
    ) -> list[Message]:
        snap     = state.get("risk_snapshot", {})
        risk     = state.get("risk_assessment", {})
        evidence = state.get("evidence_analysis", {})
        by_sev   = snap.get("by_severity", {})
        exposure = risk.get("financial_exposure_assessment", {})

        severity_breakdown = (
            f"Critical: {by_sev.get('critical', 0)}, "
            f"High: {by_sev.get('high', 0)}, "
            f"Medium: {by_sev.get('medium', 0)}, "
            f"Low: {by_sev.get('low', 0)}"
        )

        anomalies = json.dumps(
            evidence.get("recurring_anomalies", [])[:5], indent=2, default=str
        )

        user_content = _USER_TEMPLATE.format(
            case_id=state["case_id"],
            case_context=json.dumps(state.get("case", {}), default=str),
            total_findings=snap.get("total_findings", 0),
            severity_breakdown=severity_breakdown,
            rules_triggered=json.dumps(list(snap.get("findings_by_rule", {}).keys())),
            window_start=snap.get("temporal_window", {}).get("start", "unknown"),
            window_end=snap.get("temporal_window", {}).get("end", "unknown"),
            pattern_summary=evidence.get("pattern_summary", "Not yet analyzed"),
            risk_level=risk.get("overall_risk_level", "unknown"),
            exposure_min=exposure.get("minimum_estimate_usd", 0),
            exposure_max=exposure.get("maximum_estimate_usd", 0),
            regulatory_risk=json.dumps(risk.get("regulatory_risk", {})),
            remediation_urgency=risk.get("remediation_urgency", "unknown"),
            escalation_recommended=risk.get("escalation_recommended", False),
            escalation_rationale=risk.get("escalation_rationale", "Not provided"),
            anomalies=anomalies,
        )

        system_content = self.role.to_system_block() + _DOMAIN_INSTRUCTIONS

        return [
            Message(role="system", content=system_content),
            Message(role="user",   content=user_content),
        ]

    def _parse_response(self, response: LLMResponse) -> tuple[dict, Optional[float]]:
        structured = response.structured or {}
        confidence = float(structured.get("confidence_score", 0.85))
        return structured, confidence

    def _state_update_key(self, output: dict) -> dict:
        return {"narrative": output}
