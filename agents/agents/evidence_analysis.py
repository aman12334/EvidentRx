"""
EvidenceAnalysisAgent — analyzes confirmed findings to identify patterns,
temporal anomalies, and operational correlations.

IMPORTANT: This agent analyzes evidence produced by the deterministic rules
engine. It does NOT create new violation findings. It reasons about what
the rules engine already confirmed.
"""
from __future__ import annotations

import json
from typing import ClassVar

from agents.agents.base import BaseAgent
from agents.llm.base import LLMResponse, Message
from agents.memory.case import CaseMemory
from agents.memory.workflow import WorkflowMemory
from agents.roles import EVIDENCE_ANALYST, AgentRole
from agents.schemas.inputs import EvidenceAnalysisInput
from agents.state import InvestigationState

_DOMAIN_INSTRUCTIONS = """You are operating within an enterprise 340B pharmaceutical compliance platform.

The deterministic rules engine is the ONLY source of truth for whether a violation occurred.
Your job is pattern detection and correlation — not violation determination.

Output ONLY valid JSON in the exact schema specified. Do not add commentary outside the JSON block.

ANALYSIS FOCUS:
1. Operational patterns (same pharmacy, same prescriber cluster, same NDC family, same payer)
2. Temporal clustering (when did violations concentrate, and what does that imply?)
3. Severity distribution and what it implies about root cause
4. Whether this appears systemic vs. isolated
5. Data quality concerns that may affect audit defensibility

Respond with this exact JSON structure:
{
  "pattern_summary": "<one paragraph describing the dominant violation pattern>",
  "temporal_analysis": "<when violations are concentrated and what that implies>",
  "severity_assessment": "<what the severity distribution tells us>",
  "systemic_vs_isolated": "systemic|isolated|unclear",
  "root_cause_hypotheses": ["<hypothesis 1>", "<hypothesis 2>"],
  "recurring_anomalies": [
    {"anomaly": "<description>", "finding_count": <int>, "significance": "high|medium|low"}
  ],
  "data_quality_concerns": ["<concern 1>", "<concern 2>"],
  "audit_defensibility_score": <float 0.0-1.0>,
  "confidence_score": <float 0.0-1.0>,
  "analyst_notes": "<additional context an auditor should know>"
}"""

_USER_TEMPLATE = """Analyze the following 340B compliance findings for investigation case {case_id}.

CASE CONTEXT:
{case_context}

RISK SNAPSHOT:
{risk_snapshot}

CONFIRMED FINDINGS ({finding_count} total):
{findings_json}

EVIDENCE SUMMARIES (extracted from rules engine evidence payloads):
{evidence_summaries}

PRIOR INVESTIGATION CONTEXT:
{prior_context}"""


class EvidenceAnalysisAgent(BaseAgent):
    agent_type = "evidence_analysis"
    task_type  = "evidence_analysis"
    role: ClassVar[AgentRole] = EVIDENCE_ANALYST
    input_schema = EvidenceAnalysisInput

    def _extract_input(self, state: InvestigationState) -> dict:
        return {
            "case_id":       state["case_id"],
            "findings":      state.get("findings", []),
            "risk_snapshot": state.get("risk_snapshot", {}),
        }

    def _build_messages(
        self,
        state: InvestigationState,
        case_memory: CaseMemory,
        workflow_memory: WorkflowMemory,
    ) -> list[Message]:
        findings = state.get("findings", [])

        # Build evidence summaries from the evidence_payload fields
        # Cap to top 5 by severity to stay within free-tier token limits
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings_sorted = sorted(
            findings, key=lambda f: severity_order.get(f.get("severity", "low"), 4)
        )
        evidence_summaries = []
        for f in findings_sorted[:5]:   # cap at 5 to manage context length
            payload = f.get("evidence_payload", {})
            if isinstance(payload, str):
                import json as _j
                try:
                    payload = _j.loads(payload)
                except Exception:
                    payload = {}
            evidence_summaries.append({
                "finding_code": f.get("finding_code"),
                "rule_code":    f.get("rule_code"),
                "severity":     f.get("severity"),
                "trigger":      payload.get("trigger"),
                "service_date": payload.get("service_date"),
                "ndc_11":       payload.get("ndc_11"),
            })

        user_content = _USER_TEMPLATE.format(
            case_id=state["case_id"],
            case_context=json.dumps(state.get("case", {}), default=str),
            risk_snapshot=json.dumps(state.get("risk_snapshot", {}), default=str),
            finding_count=len(findings),
            findings_json=json.dumps(findings_sorted[:5], default=str),
            evidence_summaries=json.dumps(evidence_summaries, default=str),
            prior_context=case_memory.to_prompt_context(),
        )

        system_content = self.role.to_system_block() + _DOMAIN_INSTRUCTIONS

        return [
            Message(role="system", content=system_content),
            Message(role="user",   content=user_content),
        ]

    def _parse_response(self, response: LLMResponse) -> tuple[dict, float | None]:
        structured = response.structured or {}
        confidence = float(structured.get("confidence_score", 0.8))
        return structured, confidence

    def _state_update_key(self, output: dict) -> dict:
        return {
            "evidence_analysis": output,
            "patterns":          output.get("recurring_anomalies", []),
        }
