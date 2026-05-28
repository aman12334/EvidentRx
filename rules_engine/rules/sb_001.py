"""
SB-001: Accumulator Imbalance — Dispenses Exceed 340B Purchases in Period
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    if (
        ctx.accumulator_balance is not None
        and ctx.accumulator_balance < Decimal("0")
    ):
        return build_finding(
            ctx, rule_id, "SB-001", rule_version, "split_billing", "high",
            evidence_extra={
                "trigger": "negative_accumulator_balance",
                "accumulator_balance": str(ctx.accumulator_balance),
                "deficit": str(abs(ctx.accumulator_balance)),
            },
        )
    return None
