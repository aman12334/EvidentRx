"""
Pydantic input schemas for investigation agents.

Each agent declares an `input_schema` class attribute pointing to one of
these models. BaseAgent.invoke() validates the extracted state fields
against the schema before calling _build_messages(). Validation failure
returns a structured error into the workflow state — it never crashes
the graph.

Validation enforces:
  - Required fields are present in the workflow state
  - Fields have the correct type
  - Prior agents ran successfully (cross-field validators)
  - Findings are non-empty where the agent needs them
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class EvidenceAnalysisInput(BaseModel):
    """
    Input contract for EvidenceAnalysisAgent.
    Requires at least one confirmed finding with a rule_code.
    """
    case_id: str = Field(..., description="UUID string of the investigation case")
    findings: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description="Confirmed audit findings from the rules engine — must be non-empty",
    )
    risk_snapshot: dict[str, Any] = Field(
        ...,
        description="Latest risk snapshot produced by EvidenceAggregationService",
    )

    @model_validator(mode="after")
    def findings_have_required_fields(self) -> "EvidenceAnalysisInput":
        required = {"rule_code", "severity"}
        for i, f in enumerate(self.findings[:5]):  # spot-check first 5
            missing = required - f.keys()
            if missing:
                raise ValueError(
                    f"Finding at index {i} is missing required fields: {missing}. "
                    "Ensure the rules engine has fully populated audit_findings."
                )
        return self


class RiskPrioritizationInput(BaseModel):
    """
    Input contract for RiskPrioritizationAgent.
    Requires evidence_analysis to have been produced by the prior agent.
    """
    case_id: str = Field(..., description="UUID string of the investigation case")
    risk_snapshot: dict[str, Any] = Field(..., description="Latest risk snapshot")
    evidence_analysis: dict[str, Any] = Field(
        ...,
        description="Output from EvidenceAnalysisAgent — must be non-empty",
    )

    @model_validator(mode="after")
    def evidence_analysis_is_complete(self) -> "RiskPrioritizationInput":
        if not self.evidence_analysis:
            raise ValueError(
                "evidence_analysis is empty. EvidenceAnalysisAgent must run "
                "successfully before RiskPrioritizationAgent."
            )
        if "confidence_score" not in self.evidence_analysis:
            raise ValueError(
                "evidence_analysis is missing confidence_score. "
                "EvidenceAnalysisAgent may have returned a partial or failed response."
            )
        if "systemic_vs_isolated" not in self.evidence_analysis:
            raise ValueError(
                "evidence_analysis is missing systemic_vs_isolated. "
                "EvidenceAnalysisAgent output does not conform to the output contract."
            )
        return self


class NarrativeGenerationInput(BaseModel):
    """
    Input contract for ComplianceNarrativeAgent.
    Requires both prior agents to have run successfully.
    """
    case_id: str = Field(..., description="UUID string of the investigation case")
    risk_snapshot: dict[str, Any] = Field(..., description="Latest risk snapshot")
    evidence_analysis: dict[str, Any] = Field(
        ...,
        description="Output from EvidenceAnalysisAgent",
    )
    risk_assessment: dict[str, Any] = Field(
        ...,
        description="Output from RiskPrioritizationAgent — must be non-empty",
    )

    @model_validator(mode="after")
    def prior_agents_completed(self) -> "NarrativeGenerationInput":
        if not self.risk_assessment:
            raise ValueError(
                "risk_assessment is empty. RiskPrioritizationAgent must run "
                "successfully before ComplianceNarrativeAgent."
            )
        if "overall_risk_level" not in self.risk_assessment:
            raise ValueError(
                "risk_assessment is missing overall_risk_level. "
                "RiskPrioritizationAgent output does not conform to the output contract."
            )
        if "escalation_recommended" not in self.risk_assessment:
            raise ValueError(
                "risk_assessment is missing escalation_recommended. "
                "RiskPrioritizationAgent output does not conform to the output contract."
            )
        return self
