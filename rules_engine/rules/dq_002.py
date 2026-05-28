"""
DQ-002: NDC Not Found in FDA Drug Directory
"""
from __future__ import annotations

from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    # ndc_known is set by the engine's query join — None means not found in ref.ndc_drugs
    if ctx.extra.get("ndc_known") is False:
        return build_finding(
            ctx, rule_id, "DQ-002", rule_version, "data_quality", "low",
            evidence_extra={
                "trigger": "ndc_not_in_fda_directory",
                "ndc_11": ctx.ndc_11,
            },
        )
    return None
