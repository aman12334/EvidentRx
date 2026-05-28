"""
MEO-002: Medicaid Carve-In Inconsistency — Medicaid Billed Without 340B Purchase Under Carve-In
"""
from __future__ import annotations

from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    # Carve-in: CE participates in Medicaid rebates (no carve-out), but drug
    # was NOT purchased at 340B pricing — creates fraud exposure
    if (
        ctx.has_carve_out_election is False
        and ctx.is_medicaid_billed
        and not ctx.is_340b_purchase
    ):
        return build_finding(
            ctx, rule_id, "MEO-002", rule_version, "carve_in_out", "high",
            evidence_extra={
                "trigger": "carve_in_active_medicaid_billed_non_340b_purchase",
                "carve_in_active": not ctx.has_carve_out_election,
            },
        )
    return None
