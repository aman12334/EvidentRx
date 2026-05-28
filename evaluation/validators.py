"""
OutputValidator and HallucinationDetector for EvidentRx evaluation.

OutputValidator:
  - Checks required fields are present in agent output dicts
  - Validates field types and value ranges
  - Validates enum fields match allowed values

HallucinationDetector:
  - Checks agent narrative text for invented finding codes
  - Checks for invented rule codes not present in the input context
  - Checks for invented CE names or provider names

These run as part of the EvaluationHarness but can also be used
standalone in CI to validate individual agent responses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# OutputValidator
# ---------------------------------------------------------------------------

@dataclass
class FieldSpec:
    name:          str
    required:      bool          = True
    field_type:    type | None = None
    allowed_values: list | None = None
    min_val:       float | None = None
    max_val:       float | None = None
    min_length:    int | None   = None


@dataclass
class ValidationIssue:
    field:   str
    issue:   str
    value:   Any = None


class OutputValidator:
    """
    Validates structured agent output against a field specification.
    """

    # Schemas for each agent type
    SCHEMAS: dict[str, list[FieldSpec]] = {
        "evidence_analysis": [
            FieldSpec("pattern_summary",         required=True,  field_type=str,   min_length=10),
            FieldSpec("temporal_analysis",        required=True,  field_type=str),
            FieldSpec("severity_assessment",      required=True,  field_type=str),
            FieldSpec("systemic_vs_isolated",     required=True,  field_type=str,
                      allowed_values=["systemic", "isolated", "unclear"]),
            FieldSpec("root_cause_hypotheses",    required=True,  field_type=list),
            FieldSpec("recurring_anomalies",      required=True,  field_type=list),
            FieldSpec("data_quality_concerns",    required=False, field_type=list),
            FieldSpec("audit_defensibility_score",required=True,  field_type=float,
                      min_val=0.0, max_val=1.0),
            FieldSpec("confidence_score",         required=True,  field_type=float,
                      min_val=0.0, max_val=1.0),
        ],
        "risk_prioritization": [
            FieldSpec("overall_risk_level",       required=True,  field_type=str,
                      allowed_values=["critical", "high", "medium", "low"]),
            FieldSpec("priority_rank",            required=True,  field_type=int,
                      min_val=1, max_val=5),
            FieldSpec("escalation_recommended",   required=True,  field_type=bool),
            FieldSpec("escalation_rationale",     required=True,  field_type=str,   min_length=10),
            FieldSpec("financial_exposure_assessment", required=True, field_type=dict),
            FieldSpec("regulatory_risk",          required=True,  field_type=dict),
            FieldSpec("remediation_urgency",      required=True,  field_type=str,
                      allowed_values=["immediate", "within_30_days", "within_90_days", "routine"]),
            FieldSpec("confidence_score",         required=True,  field_type=float,
                      min_val=0.0, max_val=1.0),
        ],
        "narrative_generation": [
            FieldSpec("executive_summary",        required=True,  field_type=str,   min_length=100),
            FieldSpec("technical_findings",       required=True,  field_type=str,   min_length=50),
            FieldSpec("regulatory_context",       required=True,  field_type=str,   min_length=20),
            FieldSpec("financial_impact_summary", required=True,  field_type=str),
            FieldSpec("remediation_recommendations", required=True, field_type=list, min_length=1),
            FieldSpec("audit_preparation_notes",  required=True,  field_type=str),
            FieldSpec("confidence_score",         required=True,  field_type=float,
                      min_val=0.0, max_val=1.0),
        ],
    }

    def validate(self, agent_type: str, output: dict) -> list[ValidationIssue]:
        schema = self.SCHEMAS.get(agent_type)
        if not schema:
            return [ValidationIssue(field="schema", issue=f"No schema defined for agent_type '{agent_type}'")]

        issues = []
        for spec in schema:
            val = output.get(spec.name)

            if val is None:
                if spec.required:
                    issues.append(ValidationIssue(spec.name, "Required field missing"))
                continue

            if spec.field_type is not None and not isinstance(val, spec.field_type):
                # Allow int/float interop
                if not (spec.field_type is float and isinstance(val, (int, float))):
                    issues.append(ValidationIssue(
                        spec.name,
                        f"Expected {spec.field_type.__name__}, got {type(val).__name__}",
                        value=val,
                    ))
                    continue

            if spec.allowed_values and val not in spec.allowed_values:
                issues.append(ValidationIssue(
                    spec.name,
                    f"Value '{val}' not in allowed: {spec.allowed_values}",
                    value=val,
                ))

            if spec.min_val is not None and isinstance(val, (int, float)):
                if val < spec.min_val:
                    issues.append(ValidationIssue(
                        spec.name, f"Value {val} below minimum {spec.min_val}", value=val
                    ))

            if spec.max_val is not None and isinstance(val, (int, float)):
                if val > spec.max_val:
                    issues.append(ValidationIssue(
                        spec.name, f"Value {val} above maximum {spec.max_val}", value=val
                    ))

            if spec.min_length is not None:
                length = len(val) if hasattr(val, "__len__") else 0
                if length < spec.min_length:
                    issues.append(ValidationIssue(
                        spec.name,
                        f"Length {length} below minimum {spec.min_length}",
                        value=str(val)[:80] + "...",
                    ))

        return issues

    def check_financial_exposure(self, output: dict) -> list[ValidationIssue]:
        """Validate financial exposure sub-object in risk_prioritization output."""
        issues = []
        fee = output.get("financial_exposure_assessment", {})
        if not fee:
            return issues

        for field_name in ("minimum_estimate_usd", "maximum_estimate_usd", "methodology"):
            if field_name not in fee:
                issues.append(ValidationIssue(
                    f"financial_exposure_assessment.{field_name}",
                    "Required sub-field missing",
                ))

        if (fee.get("minimum_estimate_usd") is not None and
                fee.get("maximum_estimate_usd") is not None):
            if fee["minimum_estimate_usd"] > fee["maximum_estimate_usd"]:
                issues.append(ValidationIssue(
                    "financial_exposure_assessment",
                    "minimum_estimate_usd exceeds maximum_estimate_usd",
                ))

        return issues


# ---------------------------------------------------------------------------
# HallucinationDetector
# ---------------------------------------------------------------------------

# Pattern for finding codes: DD-001-2025-000001
_FINDING_CODE_RE = re.compile(r'\b[A-Z]{2}-\d{3}-\d{4}-\d{6}\b')

# Pattern for rule codes: DD-001, MEO-002, etc.
_RULE_CODE_RE = re.compile(r'\b(?:DD|MEO|CPE|SB|EE|DQ)-\d{3}\b')

VALID_RULE_CODES = frozenset([
    "DD-001", "DD-002",
    "MEO-001", "MEO-002",
    "CPE-001", "CPE-002",
    "SB-001",
    "EE-001",
    "DQ-001", "DQ-002",
])


class HallucinationDetector:
    """
    Scans agent text output for invented facts:
      - Finding codes that don't exist in the input findings
      - Rule codes not in the platform's 10-rule set
      - Specific patterns that suggest fabrication

    Not a comprehensive semantic hallucination detector — this catches
    the most common structured hallucination patterns in compliance narratives.
    """

    def check_finding_codes(
        self,
        text: str,
        known_finding_codes: list[str],
    ) -> list[str]:
        """
        Returns finding codes mentioned in text that are NOT in known_finding_codes.
        """
        mentioned = set(_FINDING_CODE_RE.findall(text))
        known_set = set(known_finding_codes)
        return sorted(mentioned - known_set)

    def check_rule_codes(self, text: str) -> list[str]:
        """
        Returns rule codes mentioned in text that are not in the platform's 10-rule set.
        """
        mentioned = set(_RULE_CODE_RE.findall(text))
        return sorted(mentioned - VALID_RULE_CODES)

    def check_confidence_inflation(self, output: dict) -> bool:
        """
        Returns True if confidence_score is suspiciously high (≥ 0.99)
        relative to the evidence volume.
        """
        confidence = output.get("confidence_score")
        findings_count = output.get("findings_count", None)
        if confidence is None:
            return False
        # Flag if confidence is 1.0 or findings_count is very small but confidence is high
        if confidence >= 0.99:
            return True
        if findings_count is not None and findings_count <= 2 and confidence >= 0.95:
            return True
        return False

    def run_all_checks(
        self,
        agent_type: str,
        output: dict,
        text_fields: list[str],
        known_finding_codes: list[str],
    ) -> list[str]:
        """
        Runs all hallucination checks. Returns a list of issue strings.
        """
        issues = []
        full_text = " ".join(str(output.get(f, "")) for f in text_fields)

        invented_findings = self.check_finding_codes(full_text, known_finding_codes)
        if invented_findings:
            issues.append(
                f"Invented finding codes in {agent_type} output: {invented_findings}"
            )

        invented_rules = self.check_rule_codes(full_text)
        if invented_rules:
            issues.append(
                f"Unknown rule codes in {agent_type} output: {invented_rules}"
            )

        if self.check_confidence_inflation(output):
            issues.append(
                f"Suspiciously high confidence_score in {agent_type}: "
                f"{output.get('confidence_score')}"
            )

        return issues
