"""
CPE-001: Dispense at Unregistered Contract Pharmacy
"""
from __future__ import annotations

from uuid import UUID

from rules_engine.context import RuleContext
from rules_engine.finding_builder import build_finding


def evaluate(ctx: RuleContext, rule_id: UUID, rule_version: str) -> dict | None:
    # cp_registered=False means the pharmacy was not an active CP at service_date
    if ctx.is_340b_purchase and ctx.cp_registered is False:
        return build_finding(
            ctx, rule_id, "CPE-001", rule_version, "contract_pharmacy_eligibility", "critical",
            evidence_extra={
                "trigger": "340b_dispense_at_unregistered_pharmacy",
                "cp_registered": ctx.cp_registered,
            },
        )
    return None
