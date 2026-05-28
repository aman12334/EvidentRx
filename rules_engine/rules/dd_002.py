"""
DD-002: Duplicate Discount — 340B Purchase + Medicaid Managed Care Without Carve-Out
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> Optional[dict]:
    # 340B purchase billed to medicaid without a carve-out election in effect
    if (
        ctx.is_340b_purchase
        and ctx.is_medicaid_billed
        and ctx.patient_id_hash
        and not ctx.has_carve_out_election
    ):
        return build_finding(
            ctx, rule_id, "DD-002", rule_version, "duplicate_discount", "high",
            evidence_extra={
                "trigger": "340b_purchase_medicaid_no_carve_out",
                "carve_out_active": ctx.has_carve_out_election,
            },
        )
    return None
