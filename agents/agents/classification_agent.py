"""
ClassificationAgent — fast, cheap label assignment using llama-3.1-8b-instant.

Runs first in the workflow (after case_intake) to:
  - Classify the violation category
  - Assign a severity label
  - Determine case priority bucket
  - Flag whether deep analysis is needed (complex/ambiguous cases)

Intentionally lightweight — 8B model, low max_tokens, fast turnaround.
Does NOT reason about violations. Only categorises what the rules engine confirmed.
"""
from __future__ import annotations

from typing import ClassVar, Optional

from agents.agents.base import BaseAgent
from agents.llm.base import LLMResponse, Message
from agents.memory.case import CaseMemory
from agents.memory.workflow import WorkflowMemory
from agents.state import InvestigationState

_SYSTEM = """You are a 340B compliance case classifier. Your only job is to assign labels.

Rules:
- Output ONLY valid JSON — no commentary, no markdown fences
- Base every label strictly on the input data provided
- Do NOT reason about whether violations occurred — the rules engine already confirmed them
- Flag needs_deep_analysis=true when: critical findings > 5, OR systemic patterns present,
  OR financial_exposure > 100000, OR case_type is regulatory_inquiry

Output this exact JSON:
{
  "violation_category": "duplicate_dispensing|medicaid_overlap|patient_eligibility|contract_pharmacy|split_billing|data_quality|regulatory_inquiry|other",
  "severity_label": "critical|high|medium|low",
  "priority_bucket": "immediate|urgent|routine|low_priority",
  "case_complexity": "simple|moderate|complex",
  "needs_deep_analysis": true|false,
  "flags": ["<flag1>", "<flag2>"],
  "confidence": <float 0.0-1.0>
}"""

_USER = """Classify this 340B investigation case.

case_type: {case_type}
status: {status}
priority: {priority}
total_findings: {total_findings}
critical_findings: {critical_findings}
high_findings: {high_findings}
financial_exposure: {financial_exposure}
risk_score: {risk_score}
ndc_count: {ndc_count}
unique_patients: {unique_patients}"""


class ClassificationAgent(BaseAgent):
    agent_type = "classification"
    task_type  = "classification"         # → llama-3.1-8b-instant

    def _build_messages(
        self,
        state: InvestigationState,
        case_memory: CaseMemory,
        workflow_memory: WorkflowMemory,
    ) -> list[Message]:
        case = state.get("case", {})
        snap = state.get("risk_snapshot", {})

        return [
            Message(role="system", content=_SYSTEM),
            Message(role="user", content=_USER.format(
                case_type=case.get("case_type", "unknown"),
                status=case.get("status", "unknown"),
                priority=case.get("priority", "unknown"),
                total_findings=snap.get("total_findings", 0),
                critical_findings=snap.get("critical_findings", 0),
                high_findings=snap.get("high_findings", 0),
                financial_exposure=snap.get("total_financial_exposure", 0),
                risk_score=snap.get("composite_risk_score", 0),
                ndc_count=len(snap.get("ndc_list", [])),
                unique_patients=snap.get("unique_patients", 0),
            )),
        ]

    def _parse_response(self, response: LLMResponse) -> tuple[dict, Optional[float]]:
        structured = response.structured or {}
        confidence = float(structured.get("confidence", 0.7))
        return structured, confidence

    def _state_update_key(self, output: dict) -> dict:
        return {
            "classification":    output,
            "needs_deep_analysis": bool(output.get("needs_deep_analysis", False)),
        }
