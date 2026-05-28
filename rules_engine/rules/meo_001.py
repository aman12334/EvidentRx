"""
MEO-001: Medicaid Carve-Out Violation — 340B Drug Dispensed to Medicaid Under Carve-Out
"""
from __future__ import annotations

from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    if ctx.has_carve_out_election and ctx.is_medicaid_billed and ctx.is_340b_purchase:
        return build_finding(
            ctx, rule_id, "MEO-001", rule_version, "carve_in_out", "critical",
            evidence_extra={
                "trigger": "carve_out_active_but_340b_dispensed_to_medicaid",
                "carve_out_active": ctx.has_carve_out_election,
            },
        )
    return None
