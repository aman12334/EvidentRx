"""
CPE-002: Contract Pharmacy Dispensing After Termination Date
"""
from __future__ import annotations

from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    if (
        ctx.is_340b_purchase
        and ctx.cp_termination_date is not None
        and ctx.service_date > ctx.cp_termination_date
    ):
        return build_finding(
            ctx, rule_id, "CPE-002", rule_version, "contract_pharmacy_eligibility", "high",
            evidence_extra={
                "trigger": "dispense_after_cp_termination",
                "cp_termination_date": ctx.cp_termination_date.isoformat(),
                "service_date": ctx.service_date.isoformat(),
                "days_past_termination": (ctx.service_date - ctx.cp_termination_date).days,
            },
        )
    return None
